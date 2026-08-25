"""Sources added from the dashboard, as data rather than code.

The eight built-in connectors stay in ``connectors/registry.py``. They carry
behaviour no configuration expresses - PNCP's ``modalidades``, CanadaBuys' dual
feed, TED's expert-search syntax - and rewriting them as rows would be a large
change with nothing to show for it.

A row here is the other kind of source: one somebody found, whose API is
regular enough to be described rather than programmed.

The credential is deliberately *not* a column. It lives in ``app_settings``
under ``source.{name}.credential`` like every other key, so there is one
write-only credential path instead of two, and a row can be read freely without
reading a secret.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.db import Base
from app.models.tender import utcnow

#: JSON on SQLite, JSONB on PostgreSQL. Both are migrated by the same revision
#: and both are exercised in CI, so the variant has to be declared once here.
JSONVariant = JSON().with_variant(JSONB(), "postgresql")

#: How the credential is presented to the endpoint.
AUTH_STYLES = ("none", "query", "header", "bearer")

#: What the payload is, which decides whether a mapping is needed at all.
FORMATS = ("ocds", "rss", "json")


class Source(Base):
    """One user-added source. See docs/superpowers/specs/2026-08-25-user-defined-sources-design.md."""

    __tablename__ = "sources"

    #: Slug, unique against the built-in CONNECTOR_CLASSES as well as this table.
    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(200))
    homepage: Mapped[str] = mapped_column(String(500), default="")
    url: Mapped[str] = mapped_column(Text)

    auth: Mapped[str] = mapped_column(String(16), default="none")
    #: The query parameter or header name the credential is sent as.
    auth_param: Mapped[str | None] = mapped_column(String(128), default=None)

    format: Mapped[str] = mapped_column(String(16), default="json")
    #: Field paths, for 'json' only. 'ocds' and 'rss' parse themselves.
    mapping: Mapped[dict[str, Any] | None] = mapped_column(JSONVariant, default=None)
    pagination: Mapped[dict[str, Any] | None] = mapped_column(JSONVariant, default=None)

    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Source {self.name} format={self.format} enabled={self.enabled}>"
