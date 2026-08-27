"""Who is allowed to hold an account: the workspace roster.

This is the second answer this product has given to "how does somebody get in",
and it replaces the first for the ordinary case. D25 shipped single-use invite
tokens: one token per person, shown once, delivered by an administrator. That
works, and it made every new colleague a small clerical task — issue, copy
before the box closes, paste, repeat.

The roster inverts which half is the secret. **The address is the permission.**
An administrator writes down who belongs, sends the whole team one durable join
link, and each person registers themselves. Anyone whose address is not on the
list is refused no matter what link they hold.

That inversion is what makes the join link safe to store readably and show
again, which single-use invite tokens are not: on its own the link opens nothing.
A leaked one is only useful to somebody who is already on the roster, and they
were welcome anyway. See docs/DECISIONS.md (D28).

Named ``roster`` rather than ``members`` on purpose. "Member" is already a *role*
in this system (``ROLE_MEMBER``, as against an administrator), and a
``workspace_members`` table holding rows whose role is ``admin`` would be a
sentence that argues with itself.

An entry is not an account. It is permission to create one, and it outlives the
account: removing somebody's account does not remove them from the roster, and
removing them from the roster does not close an account they already have. Both
of those are deliberate, and both are documented where they are enforced.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.tender import utcnow
from app.models.user import ROLE_MEMBER


class RosterEntry(Base):
    """One address permitted to hold an account in this workspace."""

    __tablename__ = "roster"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    #: Lowercased, like ``users.email``, and unique for the same reason: UNIQUE
    #: is case-sensitive on both engines, so "Ada@x.com" and "ada@x.com" would
    #: otherwise be two entries that look like one.
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    #: The role this person gets *when they join*. Changing it afterwards does
    #: not move an existing account - that is what the users endpoint is for -
    #: because silently re-roling somebody on a roster edit would be a surprise.
    role: Mapped[str] = mapped_column(String(16), default=ROLE_MEMBER)
    note: Mapped[str] = mapped_column(String(200), default="")

    added_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    #: Set when this address registers. Kept as a link rather than a boolean so
    #: the admin list can show *which* account it became, and so an account
    #: deleted later leaves the roster entry intact and reusable.
    joined_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )
    joined_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)

    @property
    def has_joined(self) -> bool:
        return self.joined_at is not None

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<RosterEntry {self.email} role={self.role} joined={self.has_joined}>"
