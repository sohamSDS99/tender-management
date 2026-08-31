"""Cached translations, one row per notice per target language.

A separate table rather than a column on `tenders`, for two reasons that both
matter here. The scoring and classification columns on `Tender` are frozen
(CLAUDE.md), and widening that table would put a multi-kilobyte text field on
the row every list query reads. This way `GET /api/tenders` is untouched and the
cache is only ever joined when somebody opens one notice.

It is a cache, not a record of the notice: `ON DELETE CASCADE` means dropping a
tender drops its translation, and nothing here is authoritative - deleting every
row costs one re-fetch per notice a human reopens.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.tender import utcnow


class TenderTranslation(Base):
    """One notice's description in one target language."""

    __tablename__ = "tender_translations"
    __table_args__ = (
        # The uniqueness is the cache: it is what makes "translate this notice"
        # idempotent under two people clicking at the same moment, enforced by
        # the database rather than by checking first and hoping.
        UniqueConstraint("tender_id", "target_language", name="uq_translation_tender_target"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tender_id: Mapped[int] = mapped_column(
        ForeignKey("tenders.id", ondelete="CASCADE"), index=True, nullable=False
    )
    #: Normalised two-letter code the text was translated *from*, as resolved by
    #: `translator.normalise_language` - not the raw stored value, so a row is
    #: readable without re-running the normaliser.
    source_language: Mapped[str] = mapped_column(String(8), nullable=False)
    target_language: Mapped[str] = mapped_column(String(8), nullable=False, default="en")
    #: Which provider produced it. Kept so a cache written by a provider that has
    #: since been replaced is identifiable rather than mysterious.
    provider: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    text: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<TenderTranslation tender={self.tender_id} " f"{self.source_language}->{self.target_language}>"
        )
