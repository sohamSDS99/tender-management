"""ORM models: normalized tenders and fetch runs."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def utcnow() -> datetime:
    """Naive UTC. Every datetime in the database is UTC; see source_timezone."""
    return datetime.now(UTC).replace(tzinfo=None)


class Tender(Base):
    __tablename__ = "tenders"
    __table_args__ = (
        UniqueConstraint("source", "source_notice_id", name="uq_tender_source_notice"),
        Index("ix_tender_score_deadline", "relevance_score", "deadline"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # --- identity ---
    source: Mapped[str] = mapped_column(String(64), index=True)
    source_notice_id: Mapped[str] = mapped_column(String(255))
    source_url: Mapped[str | None] = mapped_column(String(1024))
    reference_number: Mapped[str | None] = mapped_column(String(255))

    # --- content ---
    title: Mapped[str] = mapped_column(String(1024), default="")
    description: Mapped[str | None] = mapped_column(Text)
    buyer_name: Mapped[str | None] = mapped_column(String(512))
    # Wide enough for a full country name, not just an ISO code: the World Bank
    # feed emits "Indonesia" and the OCDS feeds emit whatever the buyer filed.
    # SQLite ignores VARCHAR limits, so an 8-char cap silently dropped those
    # rows only on PostgreSQL.
    buyer_country: Mapped[str | None] = mapped_column(String(64), index=True)
    delivery_location: Mapped[str | None] = mapped_column(String(512))

    # --- dates (always UTC) ---
    publication_date: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    deadline: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    source_updated_at: Mapped[datetime | None] = mapped_column(DateTime)
    source_timezone: Mapped[str | None] = mapped_column(String(64))

    # --- classification ---
    status: Mapped[str | None] = mapped_column(String(64), index=True)
    procurement_stage: Mapped[str | None] = mapped_column(String(32), index=True)
    notice_type: Mapped[str | None] = mapped_column(String(128))
    estimated_value: Mapped[float | None] = mapped_column(Float)
    currency: Mapped[str | None] = mapped_column(String(8))
    classification_codes: Mapped[list | None] = mapped_column(JSON, default=list)
    document_urls: Mapped[list | None] = mapped_column(JSON, default=list)
    language: Mapped[str | None] = mapped_column(String(16))

    # --- provenance ---
    raw_payload: Mapped[dict | None] = mapped_column(JSON)
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    # --- relevance ---
    relevance_score: Mapped[int] = mapped_column(Integer, default=0, index=True)
    relevance_category: Mapped[str | None] = mapped_column(String(64), index=True)
    relevance_reasons: Mapped[list | None] = mapped_column(JSON, default=list)
    topic_relevance_score: Mapped[int] = mapped_column(Integer, default=0)
    product_fit_score: Mapped[int] = mapped_column(Integer, default=0)
    procurement_intent_score: Mapped[int] = mapped_column(Integer, default=0)
    fit_status: Mapped[str] = mapped_column(String(32), default="not_fit", index=True)
    deployment_fit: Mapped[str] = mapped_column(String(32), default="deployment_unspecified", index=True)
    disqualifiers: Mapped[list | None] = mapped_column(JSON, default=list)
    review_flags: Mapped[list | None] = mapped_column(JSON, default=list)
    is_actionable: Mapped[bool] = mapped_column(default=True, index=True)

    # --- learned from reviewer verdicts (D26) --------------------------------
    #
    # Additive, and deliberately *beside* the relevance columns above rather
    # than mixed into them: the engine's score is still only the engine's, and
    # nothing the learner concludes can move it. These two say "this looks like
    # the notices you rejected", with the patterns that made it look that way.
    #
    # A reviewer's own verdict is not here - it lives in tender_feedback, which
    # a sweep cannot touch. `Tender.feedback` is added by that model's backref.
    auto_irrelevant: Mapped[bool] = mapped_column(default=False, index=True)
    auto_irrelevant_reasons: Mapped[list | None] = mapped_column(JSON, default=list)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class FetchRun(Base):
    __tablename__ = "fetch_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(64), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(32), default="running", index=True)
    records_received: Mapped[int] = mapped_column(Integer, default=0)
    records_created: Mapped[int] = mapped_column(Integer, default=0)
    records_updated: Mapped[int] = mapped_column(Integer, default=0)
    records_skipped: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[str | None] = mapped_column(Text)
    window_from: Mapped[datetime | None] = mapped_column(DateTime)
    window_to: Mapped[datetime | None] = mapped_column(DateTime)
    trigger: Mapped[str] = mapped_column(String(32), default="manual")
    # Groups the per-source runs of one scheduled sweep, so a run can be
    # correlated with the slack_notifications rows it produced.
    batch_id: Mapped[str | None] = mapped_column(String(64), index=True)
