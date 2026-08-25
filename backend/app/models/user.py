"""Accounts, sessions and invites.

This is the first table in the product that holds anything about a *person*, and
it reverses the position D18 took ("internal network only, deliberately no user
accounts"). The replacement position is written up in docs/DECISIONS.md (D25):
reading tenders stays open to anyone who can reach the dashboard, and an account
buys a profile rather than access. So nothing here is on the read path, and a
signed-out browser behaves exactly as it did before.

Three things worth knowing about the shape:

* **No secret is stored in a readable form.** ``User.password_hash`` is a scrypt
  digest, and both ``UserSession.token_hash`` and ``Invite.token_hash`` hold a
  SHA-256 of a token that was shown to its owner once and never written down.
  A dump of this database therefore lets nobody log in as anybody.
* **Email is stored lowercased**, because ``UNIQUE`` is case-sensitive on both
  SQLite and PostgreSQL and "Ada@x.com" registering over "ada@x.com" would
  otherwise be two accounts that look like one. ``normalise_email`` in
  ``app/services/accounts.py`` is the only writer.
* **Sessions are rows, not JWTs.** A signed token cannot be withdrawn before it
  expires, and "sign out everywhere" and "changing my password ends my other
  sessions" are both features here. Revocation has to be a write somewhere, so
  it is a write here.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.tender import utcnow

#: Can invite, deactivate and change roles. The first account created is one.
ROLE_ADMIN = "admin"
#: Can do everything a signed-out reader can, plus own a profile.
ROLE_MEMBER = "member"
ROLES = (ROLE_ADMIN, ROLE_MEMBER)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    #: Lowercased. The login identifier; there is no separate username.
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(120))
    #: ``scrypt$n$r$p$salt$key``, all base64. Never a plaintext password.
    password_hash: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(String(16), default=ROLE_MEMBER)
    #: Deactivating is the reversible alternative to deleting. A deactivated
    #: account keeps its rows (and so its invite history) but cannot sign in,
    #: and every live session of theirs is revoked at the same moment.
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    #: Consecutive failed sign-ins since the last success. Reset on success.
    failed_logins: Mapped[int] = mapped_column(Integer, default=0)
    #: Set when failed_logins crosses LOGIN_MAX_FAILURES. Naive UTC, like
    #: everything else stored here.
    locked_until: Mapped[datetime | None] = mapped_column(DateTime, default=None)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)

    @property
    def is_admin(self) -> bool:
        return self.role == ROLE_ADMIN

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<User {self.email} role={self.role} active={self.is_active}>"


class UserSession(Base):
    """One signed-in browser.

    Kept after revocation rather than deleted, so the profile's session list can
    say a session ended instead of silently losing it, and so a reused token can
    be recognised as revoked rather than merely unknown.
    """

    __tablename__ = "user_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    #: SHA-256 of the cookie value. The cookie itself is never stored.
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    #: Touched on use, which is what makes the sliding expiry work and what the
    #: profile shows as "last active".
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)

    #: Truncated. Shown in the session list so a person can recognise which of
    #: their own browsers a row is, and nothing else reads it.
    user_agent: Mapped[str] = mapped_column(String(400), default="")

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<UserSession user={self.user_id} expires={self.expires_at}>"


class Invite(Base):
    """A single-use, expiring permission to create one account.

    Registration is open only until the first account exists; after that an
    invite is required (D25). The token is single-use because an invite that
    could be redeemed twice is not an invite, it is open registration with an
    extra step.
    """

    __tablename__ = "invites"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    #: SHA-256 of the token handed to the admin. Shown once, at creation.
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    #: Optional. When set, registration must use this exact address, which turns
    #: a forwarded link into a useless one. When null, anyone holding the token
    #: may register — the right shape for "send this to whoever takes the role".
    email: Mapped[str | None] = mapped_column(String(320), default=None)
    role: Mapped[str] = mapped_column(String(16), default=ROLE_MEMBER)
    note: Mapped[str] = mapped_column(String(200), default="")

    created_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    accepted_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    accepted_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )
    #: Withdrawn before use. Distinct from expiry so the list can say which.
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)

    def status(self, now: datetime) -> str:
        """One of ``accepted``, ``revoked``, ``expired``, ``pending``.

        Order matters: an invite that was accepted and has since passed its
        expiry is *accepted*, not expired, because that is what happened to it.
        """
        if self.accepted_at is not None:
            return "accepted"
        if self.revoked_at is not None:
            return "revoked"
        if self.expires_at <= now:
            return "expired"
        return "pending"

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Invite {self.id} role={self.role} expires={self.expires_at}>"
