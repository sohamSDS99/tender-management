"""The workspace roster, and the join link that goes with it.

Two pieces, and the relationship between them is the whole design:

* the **roster** is a list of addresses permitted to hold an account
* the **join link** is a durable token that everybody shares

Neither works alone. The link without a roster entry is refused; a roster entry
without the link cannot register. That is what lets the link be stored readably,
handed out to a whole team, and shown again next month — on its own it opens
nothing, and the only people it helps are people already welcome.

Contrast with the single-use invites in ``accounts.py``, which are still here for
the occasional outsider: there the *token* is the permission, so it is hashed,
single-use, expiring, and unrecoverable once shown. Both mechanisms exist because
they answer different questions — "let my team in" and "let this one person in".
"""

from __future__ import annotations

import logging
import secrets

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.logging_config import log_ctx
from app.models import KEY_JOIN_TOKEN, ROLES, AppSetting, RosterEntry, User, utcnow
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


# --- the join token ---------------------------------------------------------


def get_join_token(db: Session) -> str | None:
    """The current token, or None if no link has ever been created."""
    row = db.get(AppSetting, KEY_JOIN_TOKEN)
    return row.value if row and row.value else None


def rotate_join_token(db: Session) -> str:
    """Mint a new token, invalidating the previous one.

    The only way to withdraw a link that has been shared too widely. Everyone
    who has not joined yet needs the new link; everyone who already has an
    account is unaffected, because the token grants registration and nothing
    else.
    """
    token = secrets.token_urlsafe(TOKEN_BYTES)
    row = db.get(AppSetting, KEY_JOIN_TOKEN)
    if row is None:
        db.add(AppSetting(key=KEY_JOIN_TOKEN, value=token))
    else:
        row.value = token
    db.commit()
    log_ctx(logger, logging.INFO, "workspace join link rotated")
    return token


def join_url(token: str, settings: Settings) -> str:
    """Where to send the team. Read by AuthPage exactly like ``?invite=``."""
    return f"{settings.app_base_url}/?join={token}"


def token_matches(db: Session, candidate: str) -> bool:
    """Constant-time comparison against the stored token.

    Compared in constant time even though the roster is the real gate: a timing
    oracle on this value would let somebody discover the link, and the link plus
    a guess at a colleague's address is a worse position than either alone.
    """
    current = get_join_token(db)
    if not current or not candidate:
        return False
    return secrets.compare_digest(candidate, current)


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
    """Change the role a *future* account will get.

    Deliberately does not touch an account that already exists. Somebody who
    joined last week keeps the role they were given; moving them is what
    PATCH /api/auth/users is for, and doing it as a side effect of a roster edit
    would be a change nobody asked for happening in the wrong place.
    """
    if role not in ROLES:
        raise RosterError(f"Unknown role {role!r}.")
    entry.role = role
    db.commit()
    return entry


def remove_entry(db: Session, actor: User, entry: RosterEntry) -> None:
    """Take an address off the roster.

    **This does not close an account they already have**, and that is not an
    oversight. Removing an address withdraws permission to *register*; ending
    an existing person's access is a different act with different consequences
    (their sessions die, their history stays), and it lives behind
    ``PATCH /api/auth/users/{id}`` where the last-administrator guard applies.
    Conflating the two would let a roster tidy-up lock out the only admin.
    """
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


def counts(db: Session) -> dict[str, int]:
    total = int(db.execute(select(func.count(RosterEntry.id))).scalar_one())
    joined = int(
        db.execute(select(func.count(RosterEntry.id)).where(RosterEntry.joined_at.isnot(None))).scalar_one()
    )
    return {"total": total, "joined": joined, "waiting": total - joined}
