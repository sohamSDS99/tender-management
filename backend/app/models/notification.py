"""Slack delivery ledger.

One row per (tender, channel). The unique constraint is the whole point: it is
what makes a retried, delayed or double-fired run unable to announce the same
tender twice. See app/services/notifier.py for the claim -> post -> settle
sequence that uses it.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.tender import utcnow

# status values
CLAIMED = "pending"
SENT = "sent"
FAILED = "failed"
# The POST left this process but no response came back, so we cannot know
# whether Slack rendered it. Treated as delivered for the purpose of never
# double-posting, and surfaced as a degraded state for a human to resolve.
UNCONFIRMED = "unconfirmed"


class SlackNotification(Base):
    __tablename__ = "slack_notifications"
    __table_args__ = (
        # A tender is announced at most once per channel, for all time. Keying on
        # the run instead would let the *next* run re-announce the same tender,
        # which is the duplicate we actually care about.
        UniqueConstraint("tender_id", "channel_label", name="uq_slack_notification_tender_channel"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tender_id: Mapped[int] = mapped_column(ForeignKey("tenders.id", ondelete="CASCADE"), index=True)
    channel_label: Mapped[str] = mapped_column(String(64), default="#tenders")

    # Provenance: which run claimed this tender, and which one settled it.
    run_batch_id: Mapped[str] = mapped_column(String(64), index=True)
    trigger: Mapped[str] = mapped_column(String(32), default="cron")

    status: Mapped[str] = mapped_column(String(32), default=CLAIMED, index=True)
    claimed_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    posted_at: Mapped[datetime | None] = mapped_column(DateTime)
    response_code: Mapped[int | None] = mapped_column(Integer)
    # Redacted before it is written; never contains the webhook URL.
    error_message: Mapped[str | None] = mapped_column(Text)

    # Snapshot of what was announced, so the digest can be reconstructed later
    # without re-reading a tender that may since have been amended.
    relevance_score_at_send: Mapped[int | None] = mapped_column(Integer)
