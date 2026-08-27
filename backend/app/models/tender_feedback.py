"""A reviewer's verdict on one notice - and the corpus the learner reads.

Its own table rather than columns on ``tenders`` for one reason: a notice is a
record of what a buyer published, and a verdict is a record of what we thought
of it. Every sweep rewrites the first, and must never be able to touch the
second. It also means a verdict survives a re-score, a re-ingest and a content
hash change without anything having to remember to preserve it.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, backref, mapped_column, relationship

from app.db import Base
from app.models.tender import Tender, utcnow

RELEVANT = "relevant"
IRRELEVANT = "irrelevant"
VERDICTS = (RELEVANT, IRRELEVANT)


class TenderFeedback(Base):
    __tablename__ = "tender_feedback"

    #: One current verdict per notice, so re-marking is an update and the table
    #: cannot disagree with itself. The primary key *is* the tender.
    tender_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("tenders.id", ondelete="CASCADE"), primary_key=True
    )
    verdict: Mapped[str] = mapped_column(String(16), index=True)
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    #: The relationship is declared here, and `Tender.feedback` comes from the
    #: backref, so `tender.py` never has to import this module - the import runs
    #: one way only and the frozen model file stays untouched by it.
    #:
    #: `selectin` rather than a join: one extra query per page of results, and
    #: no row duplication on the tender side. Loading it eagerly is what lets
    #: the API answer "what did we decide about this?" without the route having
    #: to remember to ask.
    tender: Mapped[Tender] = relationship(
        Tender,
        backref=backref(
            "feedback",
            uselist=False,
            lazy="selectin",
            cascade="all, delete-orphan",
            passive_deletes=True,
        ),
    )
