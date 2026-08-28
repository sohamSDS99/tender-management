"""The workspace roster: who belongs, and the personal link that lets them in.

Each entry carries **its own durable access link**, and that link is the whole
credential. Opening it and pressing Accept creates the account if it does not
exist yet and signs the person in. There is no password at any point, now or
later (D29).

This reverses what D28 said one day earlier, and the reversal is worth naming
because the code still has to be read in that light. D28's rule was "the address
is the permission, not the link", which is what made *one* link safe to share
with a whole team. D29 asked for the opposite: nothing to type, nothing to
remember, just accept. Once clicking is enough, the link necessarily **is** the
credential — whoever holds it is that person.

Everything that follows from that is deliberate:

* links are **per person**, never shared, because a shared one would let anybody
  who saw it become somebody
* they are **durable**, so the same link works next month on a new laptop, which
  is the only way "nothing else is needed" survives past the first session
* they are stored **readably**, so an administrator can re-send one
* they are **revocable** by setting the token to null, which is the answer to a
  link that has leaked and the reason there is no separate revoked flag to
  disagree with the token
"""

from __future__ import annotations

import logging
import secrets

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.logging_config import log_ctx
from app.models import ROLES, RosterEntry, User, utcnow
from app.services.accounts import AccountError, NotPermitted, normalise_email
from app.settings import Settings

logger = logging.getLogger(__name__)

#: Bytes of entropy in the join token, before base64.
TOKEN_BYTES = 32

#: A hard ceiling on one bulk paste. Not a business rule — a guard against
#: somebody pasting a whole mailbox export into the box and blocking a request
#: while it is validated one address at a time.
MAX_BULK_ADDRESSES = 200


class RosterError(AccountError):
    status = 422


# --- the roster -------------------------------------------------------------


def parse_addresses(raw: str) -> list[str]:
    """Split a pasted blob into normalised addresses.

    Accepts commas, semicolons, whitespace and newlines together, because that
    is what comes out of a mail client's To: field, a spreadsheet column and a
    Slack message respectively, and asking somebody to reformat a list they
    already have is the kind of friction this feature exists to remove.

    Deduplicates while preserving order, so pasting the same list twice is a
    no-op rather than an error.
    """
    chunks = raw.replace(",", " ").replace(";", " ").split()
    if len(chunks) > MAX_BULK_ADDRESSES:
        raise RosterError(f"That is more than {MAX_BULK_ADDRESSES} addresses. Add them in batches.")

    out: list[str] = []
    seen: set[str] = set()
    problems: list[str] = []
    for chunk in chunks:
        try:
            email = normalise_email(chunk)
        except AccountError:
            problems.append(chunk[:60])
            continue
        if email not in seen:
            seen.add(email)
            out.append(email)

    if problems:
        # Named rather than silently dropped: a typo'd address that vanishes
        # without comment becomes a colleague who cannot get in and nobody
        # knowing why.
        shown = ", ".join(problems[:3])
        more = f" and {len(problems) - 3} more" if len(problems) > 3 else ""
        raise RosterError(f"These do not look like email addresses: {shown}{more}.")
    if not out:
        raise RosterError("Enter at least one email address.")
    return out


def issue_access_token(db: Session, entry: RosterEntry) -> str:
    """Mint this person's link, replacing any previous one.

    The same call creates the first link and replaces a leaked one, because they
    are the same operation. Replacing does **not** sign the person out — the
    token grants sign-in, and a session already established stands on its own.
    Cutting somebody off entirely is revoke *plus* deactivating the account.
    """
    entry.access_token = secrets.token_urlsafe(TOKEN_BYTES)
    db.commit()
    log_ctx(logger, logging.INFO, "access link issued", entry=entry.id)
    return entry.access_token


def revoke_access_token(db: Session, entry: RosterEntry) -> None:
    """Withdraw the link. Null token means no link — never issued, or revoked."""
    entry.access_token = None
    db.commit()
    log_ctx(logger, logging.INFO, "access link revoked", entry=entry.id)


def access_url(token: str, settings: Settings) -> str:
    """The link an administrator sends. Read by AuthPage as ``?accept=``."""
    return f"{settings.app_base_url}/?accept={token}"


def entry_for_token(db: Session, token: str) -> RosterEntry | None:
    """Find whose link this is, or None.

    A plain indexed lookup rather than a constant-time scan. The token is 32
    bytes of `secrets` entropy in a unique index, so there is no useful timing
    signal to leak — and a comparison that walked every row to be "constant
    time" would be a denial-of-service surface of its own.
    """
    if not token or not token.strip():
        return None
    return db.execute(
        select(RosterEntry).where(RosterEntry.access_token == token.strip())
    ).scalar_one_or_none()


def get_entry(db: Session, email: str) -> RosterEntry | None:
    return db.execute(select(RosterEntry).where(RosterEntry.email == email)).scalar_one_or_none()


def list_entries(db: Session) -> list[RosterEntry]:
    """Not yet joined first, then alphabetical.

    The people who still need the link are the ones an administrator is looking
    for, so they are not buried under colleagues who joined weeks ago.
    """
    return list(
        db.execute(
            select(RosterEntry).order_by(RosterEntry.joined_at.isnot(None), RosterEntry.email)
        ).scalars()
    )


def add_addresses(
    db: Session, actor: User, *, raw: str, role: str, note: str
) -> tuple[list[RosterEntry], list[str]]:
    """Add a pasted list. Returns (added, already-present).

    Addresses already on the roster are reported rather than treated as an
    error, and their role is left alone: re-pasting a team list to add one
    person must not silently re-role the other nine.
    """
    if role not in ROLES:
        raise RosterError(f"Unknown role {role!r}.")
    emails = parse_addresses(raw)

    added: list[RosterEntry] = []
    existing: list[str] = []
    for email in emails:
        if get_entry(db, email) is not None:
            existing.append(email)
            continue
        entry = RosterEntry(
            email=email,
            role=role,
            note=" ".join((note or "").split())[:200],
            added_by_id=actor.id,
        )
        db.add(entry)
        added.append(entry)

    db.commit()
    log_ctx(
        logger,
        logging.INFO,
        "roster updated",
        actor=actor.id,
        added=len(added),
        already_present=len(existing),
    )
    return added, existing


def set_entry_role(db: Session, entry: RosterEntry, role: str) -> RosterEntry:
    """Change the role a *future* account will get, and withdraw the old link.

    Deliberately does not touch an account that already exists. Somebody who
    joined last week keeps the role they were given; moving them is what
    PATCH /api/auth/users is for, and doing it as a side effect of a roster edit
    would be a change nobody asked for happening in the wrong place.

    **Re-roling somebody who has not joined revokes their link (D30).** Since the
    role decides where the link *lands* them — an administrator goes straight to
    the dashboard, a member is shown the accept screen — a link already sent
    would quietly start behaving differently from the one the administrator
    described when they sent it. Revoking makes that visible: the row shows no
    link, and issuing a new one is the deliberate act that says "this is now an
    administrator's link". It is also the mechanical form of the rule that the
    role is set *before* a link exists.

    Left alone once they have joined, because then the link is their only
    credential and revoking it on a roster edit that changes nothing about their
    account would lock them out for no reason.
    """
    if role not in ROLES:
        raise RosterError(f"Unknown role {role!r}.")
    if role != entry.role and not entry.has_joined and entry.has_link:
        entry.access_token = None
        log_ctx(logger, logging.INFO, "access link revoked by role change", entry=entry.id)
    entry.role = role
    db.commit()
    return entry


def remove_entry(db: Session, actor: User, entry: RosterEntry, settings: Settings | None = None) -> None:
    """Take an address off the roster.

    **This does not close an account they already have**, and that is not an
    oversight. Removing an address withdraws permission to *register*; ending
    an existing person's access is a different act with different consequences
    (their sessions die, their history stays), and it lives behind
    ``PATCH /api/auth/users/{id}`` where the last-administrator guard applies.
    Conflating the two would let a roster tidy-up lock out the only admin.
    """
    configured = (getattr(settings, "platform_admin_email", "") or "").strip()
    if configured and normalise_email(configured) == entry.email:
        # Not the account - this row is only permission to *hold* one - but
        # taking it away leaves the protected address unable to re-register if
        # its account were ever lost, which quietly undoes half the point of
        # protecting it.
        raise NotPermitted("The platform administrator cannot be removed from the roster.")
    if entry.email == actor.email:
        # Nothing breaks, but it reliably confuses: the administrator remains
        # signed in with an account that the roster no longer explains.
        raise NotPermitted("You cannot remove your own address from the roster.")
    db.delete(entry)
    db.commit()
    log_ctx(logger, logging.INFO, "roster entry removed", actor=actor.id, entry=entry.id)


def claim(db: Session, entry: RosterEntry, user: User) -> None:
    """Record that this address has become an account. Caller commits."""
    entry.joined_user_id = user.id
    entry.joined_at = utcnow()


def with_links(db: Session) -> int:
    """How many entries currently have a usable link."""
    return int(
        db.execute(
            select(func.count(RosterEntry.id)).where(RosterEntry.access_token.isnot(None))
        ).scalar_one()
    )


def counts(db: Session) -> dict[str, int]:
    total = int(db.execute(select(func.count(RosterEntry.id))).scalar_one())
    joined = int(
        db.execute(select(func.count(RosterEntry.id)).where(RosterEntry.joined_at.isnot(None))).scalar_one()
    )
    return {"total": total, "joined": joined, "waiting": total - joined}
