"""One row per problem the alarm system has seen (D43).

A problem is ``(check_key, subject)``: ``("source_failing", "pncp")``, or
``("sweep_missed", "")`` for the checks that have no subject. While it is broken
the row is open (``resolved_at`` is NULL); the data closing it stamps
``resolved_at`` and the next occurrence opens a new row. History is kept, which
is what the "fixed within one working day" figure is computed from.

The partial unique index allows one *open* row per problem. Two processes
evaluating at once (a sweep's own check and the 10-minute watchdog) cannot both
open the same alarm, so neither can send the same email twice.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.tender import utcnow


class Alarm(Base):
    __tablename__ = "alarms"
    __table_args__ = (
        Index(
            "uq_alarms_open_problem",
            "check_key",
            "subject",
            unique=True,
            sqlite_where=text("resolved_at IS NULL"),
            postgresql_where=text("resolved_at IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    check_key: Mapped[str] = mapped_column(String(64), index=True)
    # The source name for a per-source check, else "".
    subject: Mapped[str] = mapped_column(String(128), default="")
    title: Mapped[str] = mapped_column(String(255))
    # The "Why" paragraph of the email, as it read when the alarm opened.
    detail: Mapped[str] = mapped_column(Text, default="")
    opened_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    # NULL means the ALARM email has not been accepted yet: the next tick retries.
    emailed_at: Mapped[datetime | None] = mapped_column(DateTime)
    reminded_at: Mapped[datetime | None] = mapped_column(DateTime)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    # NULL on a resolved alarm means the FIXED email is still owed.
    fixed_emailed_at: Mapped[datetime | None] = mapped_column(DateTime)
