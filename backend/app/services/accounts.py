"""Registration, sign-in, sessions and invites.

Everything that knows how a credential is checked lives here; the router above
it only turns the results into HTTP. See docs/DECISIONS.md (D25) for why this
product has accounts at all, and why they gate nothing.

Three choices in here are deliberate and worth not undoing:

**Passwords are hashed with ``hashlib.scrypt`` from the standard library.**
Not because it beats argon2 — it does not — but because the alternative was a
new runtime dependency in a project whose entire dependency list fits on a
screen, for a password store that will hold single digits of rows on an
internal network. scrypt is memory-hard, ships with CPython, and the stored
format carries its own parameters, so raising the cost later is a change to one
constant and not a migration.

**Session tokens are opaque and stored hashed.** A JWT would let this module
delete a table, and would also make "sign out everywhere" and "changing your
password ends your other sessions" impossible to implement honestly — a signed
token stays valid until it expires no matter what the server thinks. Revocation
has to be a write somewhere.

**Failure is deliberately uninformative.** ``authenticate`` raises the same
error for an unknown address, a wrong password and a deactivated account, and
spends the same time on all three by verifying against a dummy hash when no
user is found. Otherwise the sign-in form is an oracle that says which of your
colleagues has an account.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import secrets
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.logging_config import log_ctx
from app.models import ROLE_ADMIN, ROLE_MEMBER, ROLES, Invite, User, UserSession, utcnow
from app.settings import Settings

logger = logging.getLogger(__name__)

# --- password hashing -------------------------------------------------------

#: scrypt cost parameters. n*r*128*p bytes of memory: 16 MiB at these values,
#: comfortably under OpenSSL's 32 MiB default ceiling, and ~50-100ms per hash on
#: the deployment host. Raising N is safe — the parameters are stored per hash,
#: so old hashes keep verifying and are rewritten on next sign-in.
SCRYPT_N = 16384
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_DKLEN = 32
SALT_BYTES = 16

#: Bytes of entropy in a session cookie or invite token, before base64.
TOKEN_BYTES = 32


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def hash_password(password: str) -> str:
    """``scrypt$n$r$p$salt$key``, every field base64 where it is bytes.

    Self-describing on purpose: the verifier reads the cost out of the stored
    string rather than out of this module, so changing the constants above
    cannot invalidate hashes written before the change.
    """
    salt = secrets.token_bytes(SALT_BYTES)
    key = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=SCRYPT_DKLEN
    )
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${_b64(salt)}${_b64(key)}"


def verify_password(password: str, stored: str) -> bool:
    """Constant-time check against a stored hash. Never raises on a bad hash."""
    try:
        scheme, n, r, p, salt_b64, key_b64 = stored.split("$")
        if scheme != "scrypt":
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(key_b64)
        actual = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(expected),
        )
    except (ValueError, TypeError, MemoryError):
        # A malformed or unreadable hash is a failed verification, not a 500.
        # Falling through to an exception here would turn one corrupt row into
        # an error the sign-in form cannot explain.
        return False
    return secrets.compare_digest(actual, expected)


#: A real hash of a value nobody knows, verified against when no user matches so
#: that "no such account" costs the same time as "wrong password". Built once,
#: lazily: computing it at import would add ~50ms to every process start,
#: including the CLI entry points and every test session.
_dummy_hash: str | None = None


def _burn_equivalent_time(password: str) -> None:
    global _dummy_hash
    if _dummy_hash is None:
        _dummy_hash = hash_password(secrets.token_urlsafe(32))
    verify_password(password, _dummy_hash)


# --- errors -----------------------------------------------------------------


class AccountError(Exception):
    """Anything a caller did wrong. ``status`` is the HTTP status it maps to.

    Carrying the status here keeps the router free of a translation table that
    would drift from the rules it describes.
    """

    status = 400

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class EmailTaken(AccountError):
    status = 409


class InvalidCredentials(AccountError):
    status = 401


class AccountLocked(AccountError):
    status = 429


class InvalidInvite(AccountError):
    status = 400


class WeakPassword(AccountError):
    status = 422


class InvalidEmail(AccountError):
    status = 422


class NotPermitted(AccountError):
    status = 403


# --- validation -------------------------------------------------------------


def normalise_email(raw: str) -> str:
    """Trim and lowercase. The only writer of the stored form.

    Deliberately not a full RFC 5322 validator — that is a famously wrong thing
    to attempt, and the consequence of accepting an odd-but-legal address here
    is nothing, because nothing is delivered to it.
    """
    email = (raw or "").strip().lower()
    if len(email) < 3 or len(email) > 320:
        raise InvalidEmail("Enter an email address.")
    local, _, domain = email.partition("@")
    if not local or not domain or "." not in domain or " " in email:
        raise InvalidEmail("That does not look like an email address.")
    return email


def check_password(password: str, email: str, settings: Settings) -> None:
    """Length, and not the address itself. Nothing more.

    Composition rules (a digit, a symbol, a capital) are not applied on purpose:
    they measurably push people towards `Password1!` and this is an internal
    tool, not a bank.
    """
    minimum = settings.password_min_length
    if len(password) < minimum:
        raise WeakPassword(f"Use at least {minimum} characters.")
    if len(password) > 200:
        # bcrypt's 72-byte truncation is not a problem for scrypt, but an
        # unbounded input is: it is memory the caller chooses.
        raise WeakPassword("That password is too long.")
    if password.strip().lower() == email.strip().lower():
        raise WeakPassword("Your password cannot be your email address.")


def clean_display_name(raw: str, email: str) -> str:
    """Falls back to the local part of the address rather than being blank.

    A nameless account shows as an empty chip in the sidebar, which reads as a
    broken page rather than as a person who skipped a field.
    """
    name = " ".join((raw or "").split())[:120]
    return name or email.partition("@")[0][:120]


# --- users ------------------------------------------------------------------


def user_count(db: Session) -> int:
    return int(db.execute(select(func.count(User.id))).scalar_one())


def admin_count(db: Session, *, active_only: bool = True) -> int:
    query = select(func.count(User.id)).where(User.role == ROLE_ADMIN)
    if active_only:
        query = query.where(User.is_active.is_(True))
    return int(db.execute(query).scalar_one())


def get_by_email(db: Session, email: str) -> User | None:
    return db.execute(select(User).where(User.email == email)).scalar_one_or_none()


def register(
    db: Session,
    *,
    email: str,
    password: str,
    display_name: str,
    invite_token: str | None,
    settings: Settings,
    join_token: str | None = None,
) -> User:
    """Create an account.

    The first account ever created needs no invite and becomes an admin — the
    alternative is a deployment with nobody able to let anybody in. Every
    account after it needs a valid invite, which is the position chosen in D25.

    There are three ways in, checked in this order:

    1. **Bootstrap** - no account exists yet, so no permission is needed and the
       account becomes an administrator. The window this opens is a real
       exposure and the runbook says so: whoever reaches a fresh deployment
       first is the administrator.
    2. **A single-use invitation** (D25), for somebody who is not on the roster.
    3. **The shared join link plus a roster entry** (D28) - the ordinary path for
       a colleague. Both halves are required.

    Anything else is refused.
    """
    email = normalise_email(email)
    check_password(password, email, settings)
    display_name = clean_display_name(display_name, email)

    # Imported here, not at module scope: app/services/roster.py imports this
    # module for normalise_email and the error types, so a top-level import
    # either way is a cycle. Same fix as scheduler._job's lazy run_once import.
    from app.services import roster as roster_service

    bootstrap = user_count(db) == 0
    invite: Invite | None = None
    entry = None
    if bootstrap:
        role = ROLE_ADMIN
    elif invite_token:
        # A single-use invitation, for somebody who is not on the roster - an
        # outsider, a contractor, a one-off. Checked first because if a caller
        # presents one it is the more specific claim.
        invite = redeem_invite(db, invite_token, email=email, settings=settings)
        role = invite.role
    elif join_token:
        # The shared workspace link. Two things must hold, and the *second* is
        # the one doing the work: the link has to be current, and the address
        # has to be on the roster. That is what makes the link safe to hand to a
        # whole team and to show again later - on its own it opens nothing (D28).
        if not roster_service.token_matches(db, join_token):
            raise InvalidInvite(
                "That join link is no longer valid. Ask an administrator for the current one."
            )
        entry = roster_service.get_entry(db, email)
        if entry is None:
            raise InvalidInvite(
                "That email address is not on this workspace's list. " "Ask an administrator to add it."
            )
        role = entry.role
    else:
        raise InvalidInvite("This dashboard is invite-only. Ask an administrator for a link.")

    if get_by_email(db, email) is not None:
        raise EmailTaken("An account already exists for that email address.")

    user = User(
        email=email,
        display_name=display_name,
        password_hash=hash_password(password),
        role=role,
        is_active=True,
        # Registering mints a session, so this *is* their first sign-in. Leaving
        # it null had the profile tell somebody "Never signed in" on the page
        # they could only be reading because they were signed in, and the admin
        # people list say it about an account that had just joined.
        last_login_at=utcnow(),
    )
    db.add(user)
    db.flush()

    if invite is not None:
        invite.accepted_at = utcnow()
        invite.accepted_by_id = user.id
    if entry is not None:
        # Recorded so the admin list can show who has taken up their place and
        # who still needs the link.
        roster_service.claim(db, entry, user)
    db.commit()

    log_ctx(
        logger,
        logging.INFO,
        "account created",
        user_id=user.id,
        role=role,
        bootstrap=bootstrap,
    )
    return user


def authenticate(db: Session, *, email: str, password: str, settings: Settings) -> User:
    """Check a sign-in, or raise. Enforces the per-account lockout.

    The lockout is on the account, not the client address: the API sits behind a
    proxy and every browser on the network would otherwise share one bucket, so
    one person fat-fingering their password could lock out the office.
    """
    try:
        email = normalise_email(email)
    except InvalidEmail:
        # Same shape of failure as a wrong password, and the same cost, so an
        # invalid address cannot be distinguished from an unregistered one.
        _burn_equivalent_time(password)
        raise InvalidCredentials("Those details do not match an account.") from None

    user = get_by_email(db, email)
    if user is None:
        _burn_equivalent_time(password)
        raise InvalidCredentials("Those details do not match an account.")

    now = utcnow()
    if user.locked_until is not None and user.locked_until > now:
        minutes = max(1, int((user.locked_until - now).total_seconds() // 60) + 1)
        raise AccountLocked(f"Too many failed attempts. Try again in about {minutes} minutes.")

    # Verified before the is_active test, and not short-circuited past it: the
    # other order answers a deactivated account in the time it takes to read a
    # row, which tells an attacker their guess found a real person.
    password_ok = verify_password(password, user.password_hash)
    if not password_ok or not user.is_active:
        user.failed_logins += 1
        if user.failed_logins >= settings.login_max_failures:
            user.locked_until = now + timedelta(minutes=settings.login_lockout_minutes)
            user.failed_logins = 0
            log_ctx(logger, logging.WARNING, "account locked after failed sign-ins", user_id=user.id)
        db.commit()
        raise InvalidCredentials("Those details do not match an account.")

    user.failed_logins = 0
    user.locked_until = None
    user.last_login_at = now
    db.commit()
    return user


def update_profile(
    db: Session,
    user: User,
    *,
    display_name: str | None,
    email: str | None,
    settings: Settings | None = None,
) -> User:
    """Change the two fields a person owns about themselves."""
    if display_name is not None:
        user.display_name = clean_display_name(display_name, user.email)
    if email is not None:
        new_email = normalise_email(email)
        if new_email != user.email:
            # The protection is keyed on the address, so letting the protected
            # account change its own address would silently switch the guard off
            # - and it is the one account whose owner is least likely to notice,
            # because nothing about their session would change. Refused rather
            # than followed: the platform administrator is a deployment
            # decision, and moving it is a deployment change.
            if is_platform_admin(user, settings):
                raise NotPermitted(
                    "The platform administrator's address is fixed by the deployment. "
                    "Change PLATFORM_ADMIN_EMAIL first."
                )
            existing = get_by_email(db, new_email)
            if existing is not None and existing.id != user.id:
                raise EmailTaken("An account already exists for that email address.")
            user.email = new_email
    db.commit()
    return user


def change_password(
    db: Session,
    user: User,
    *,
    current_password: str,
    new_password: str,
    keep_session_id: int | None,
    settings: Settings,
) -> int:
    """Rotate a password and end every other session. Returns how many ended.

    Ending the others is the point, not a nicety: the reason to change a
    password is usually that somebody else may know the old one, and leaving
    their session alive makes the change cosmetic.
    """
    if not verify_password(current_password, user.password_hash):
        raise InvalidCredentials("That is not your current password.")
    check_password(new_password, user.email, settings)
    user.password_hash = hash_password(new_password)
    revoked = revoke_sessions(db, user, except_id=keep_session_id, commit=False)
    db.commit()
    log_ctx(logger, logging.INFO, "password changed", user_id=user.id, sessions_ended=revoked)
    return revoked


def is_platform_admin(user: User, settings: Settings | None) -> bool:
    """Whether this account is the one `PLATFORM_ADMIN_EMAIL` names.

    Compared through `normalise_email` on both sides rather than by raw string,
    so a variable set with different case or a stray space still matches the
    account it is meant to protect. An unset variable protects nobody, which is
    what keeps the default behaviour identical to before this existed.
    """
    configured = getattr(settings, "platform_admin_email", "") or ""
    if not configured.strip():
        return False
    try:
        return normalise_email(configured) == normalise_email(user.email)
    except InvalidEmail:
        # A malformed variable protects nobody rather than everybody. The
        # opposite would turn one typo into a workspace nobody can administer.
        return False


def set_role(db: Session, actor: User, target: User, role: str, settings: Settings | None = None) -> User:
    """Promote or demote, refusing to remove the last way back in."""
    if role not in ROLES:
        raise AccountError(f"Unknown role {role!r}.")
    if target.role == role:
        return target
    if role != ROLE_ADMIN and is_platform_admin(target, settings):
        raise NotPermitted("The platform administrator cannot be demoted.")
    if target.role == ROLE_ADMIN and role != ROLE_ADMIN and admin_count(db) <= 1:
        raise NotPermitted("This is the only administrator. Promote someone else first.")
    target.role = role
    db.commit()
    log_ctx(logger, logging.INFO, "role changed", actor=actor.id, user_id=target.id, role=role)
    return target


def set_active(
    db: Session, actor: User, target: User, active: bool, settings: Settings | None = None
) -> User:
    """Deactivate or restore an account, ending its sessions when it goes.

    Three refusals, and they are different: you cannot deactivate yourself
    (locking yourself out with one click is not a feature), you cannot
    deactivate the only remaining administrator, and you cannot deactivate the
    platform administrator at all - that one is a fixed point regardless of how
    many other administrators exist.
    """
    if target.is_active == active:
        return target
    if not active:
        if is_platform_admin(target, settings):
            raise NotPermitted("The platform administrator cannot be deactivated.")
        if target.id == actor.id:
            raise NotPermitted("You cannot deactivate your own account.")
        if target.role == ROLE_ADMIN and admin_count(db) <= 1:
            raise NotPermitted("This is the only administrator. Promote someone else first.")
    target.is_active = active
    if not active:
        # A deactivated account with a live cookie is still signed in, which
        # makes "deactivate" mean nothing until the session happens to expire.
        revoke_sessions(db, target, except_id=None, commit=False)
        target.failed_logins = 0
        target.locked_until = None
    db.commit()
    log_ctx(logger, logging.INFO, "account active changed", user_id=target.id, active=active)
    return target


def list_users(db: Session) -> list[User]:
    return list(db.execute(select(User).order_by(User.created_at)).scalars())


# --- sessions ---------------------------------------------------------------


def _token_hash(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def start_session(db: Session, user: User, *, user_agent: str, settings: Settings) -> tuple[str, UserSession]:
    """Mint a session. Returns the raw token, which is never stored or logged."""
    raw = secrets.token_urlsafe(TOKEN_BYTES)
    session = UserSession(
        user_id=user.id,
        token_hash=_token_hash(raw),
        expires_at=utcnow() + timedelta(days=settings.session_lifetime_days),
        user_agent=(user_agent or "")[:400],
    )
    db.add(session)
    db.commit()
    return raw, session


def resolve_session(db: Session, raw_token: str, settings: Settings) -> tuple[User, UserSession] | None:
    """Look up a cookie. Returns None for anything not currently valid.

    Also slides the expiry forward, but only once a day: writing on every
    request would mean a database write per page load for a value nothing reads
    at that resolution.
    """
    if not raw_token:
        return None
    session = db.execute(
        select(UserSession).where(UserSession.token_hash == _token_hash(raw_token))
    ).scalar_one_or_none()
    if session is None or session.revoked_at is not None:
        return None

    now = utcnow()
    if session.expires_at <= now:
        return None

    user = db.get(User, session.user_id)
    if user is None or not user.is_active:
        return None

    if now - session.last_seen_at > timedelta(hours=1):
        session.last_seen_at = now
        session.expires_at = now + timedelta(days=settings.session_lifetime_days)
        db.commit()
    return user, session


def end_session(db: Session, session: UserSession) -> None:
    if session.revoked_at is None:
        session.revoked_at = utcnow()
        db.commit()


def revoke_sessions(db: Session, user: User, *, except_id: int | None, commit: bool = True) -> int:
    """Revoke every live session for a user, optionally sparing one."""
    now = utcnow()
    query = select(UserSession).where(
        UserSession.user_id == user.id,
        UserSession.revoked_at.is_(None),
        UserSession.expires_at > now,
    )
    if except_id is not None:
        query = query.where(UserSession.id != except_id)
    rows = list(db.execute(query).scalars())
    for row in rows:
        row.revoked_at = now
    if commit:
        db.commit()
    return len(rows)


def list_sessions(db: Session, user: User) -> list[UserSession]:
    """Live sessions only, newest first. An ended session is not information."""
    now = utcnow()
    return list(
        db.execute(
            select(UserSession)
            .where(
                UserSession.user_id == user.id,
                UserSession.revoked_at.is_(None),
                UserSession.expires_at > now,
            )
            .order_by(UserSession.last_seen_at.desc())
        ).scalars()
    )


# --- invites ----------------------------------------------------------------


def create_invite(
    db: Session,
    creator: User,
    *,
    email: str | None,
    role: str,
    note: str,
    settings: Settings,
) -> tuple[str, Invite]:
    """Issue one. Returns the raw token, which the caller must show exactly once."""
    if role not in ROLES:
        raise AccountError(f"Unknown role {role!r}.")
    target_email = normalise_email(email) if email else None
    if target_email and get_by_email(db, target_email) is not None:
        raise EmailTaken("That address already has an account.")

    raw = secrets.token_urlsafe(TOKEN_BYTES)
    invite = Invite(
        token_hash=_token_hash(raw),
        email=target_email,
        role=role,
        note=" ".join((note or "").split())[:200],
        created_by_id=creator.id,
        expires_at=utcnow() + timedelta(days=settings.invite_lifetime_days),
    )
    db.add(invite)
    db.commit()
    log_ctx(logger, logging.INFO, "invite issued", invite_id=invite.id, role=role, by=creator.id)
    return raw, invite


def redeem_invite(db: Session, raw_token: str, *, email: str, settings: Settings) -> Invite:
    """Validate an invite for this address. Does not mark it accepted.

    Marking happens in ``register`` once the account row actually exists, so a
    registration that fails on a duplicate address does not burn the invite.
    """
    invite = db.execute(
        select(Invite).where(Invite.token_hash == _token_hash(raw_token))
    ).scalar_one_or_none()
    if invite is None:
        raise InvalidInvite("That invitation link is not valid.")
    status = invite.status(utcnow())
    if status == "accepted":
        raise InvalidInvite("That invitation has already been used.")
    if status == "revoked":
        raise InvalidInvite("That invitation was withdrawn.")
    if status == "expired":
        raise InvalidInvite("That invitation has expired. Ask for a new one.")
    if invite.email is not None and invite.email != email:
        raise InvalidInvite(f"That invitation is for {invite.email}.")
    return invite


def revoke_invite(db: Session, invite: Invite) -> Invite:
    if invite.accepted_at is not None:
        raise NotPermitted("That invitation has already been used.")
    if invite.revoked_at is None:
        invite.revoked_at = utcnow()
        db.commit()
    return invite


def list_invites(db: Session) -> list[Invite]:
    """Newest first, accepted ones included — the list is also the audit trail."""
    return list(db.execute(select(Invite).order_by(Invite.created_at.desc())).scalars())


def invite_url(raw_token: str, settings: Settings) -> str:
    """Where to send someone so the form arrives with the token already in it."""
    return f"{settings.app_base_url}/?invite={raw_token}"


__all__ = [
    "ROLE_ADMIN",
    "ROLE_MEMBER",
    "AccountError",
    "AccountLocked",
    "EmailTaken",
    "InvalidCredentials",
    "InvalidEmail",
    "InvalidInvite",
    "NotPermitted",
    "WeakPassword",
    "admin_count",
    "authenticate",
    "change_password",
    "check_password",
    "clean_display_name",
    "create_invite",
    "end_session",
    "get_by_email",
    "hash_password",
    "invite_url",
    "list_invites",
    "list_sessions",
    "list_users",
    "normalise_email",
    "redeem_invite",
    "register",
    "resolve_session",
    "revoke_invite",
    "revoke_sessions",
    "set_active",
    "set_role",
    "start_session",
    "update_profile",
    "user_count",
    "verify_password",
]
