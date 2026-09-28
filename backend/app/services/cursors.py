"""Bookmarks for sources that page by id instead of by date (D41).

Tender Impulse has no date window: each call returns the batch after a
`lastid`, and the only way to know where to resume is to remember the last
`fetchid` whose batch was stored. That value lives here, in ``app_settings``
beside the credentials, and reaches the connector through the same overlay
they do (``credentials.settings_with_stored_credentials``) - so the source
card, the sweep planner and the sweep itself all read one value.

**Written only after the batch is stored.** The vendor's docs are blunt: store
the bookmark first, fail, and those records are skipped permanently. A
connector therefore never writes it - it reports ``next_cursor`` and ingest
calls :func:`advance` once ``store_tenders`` has returned.
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.logging_config import log_ctx
from app.models import AppSetting, utcnow

logger = logging.getLogger(__name__)


def _key(source: str) -> str:
    return f"source.{source}.cursor"


def stored(db: Session, source: str) -> str | None:
    row = db.get(AppSetting, _key(source))
    value = (row.value or "").strip() if row else ""
    return value or None


def overlay(db: Session, connector_classes: tuple[type, ...]) -> dict[str, str]:
    """Settings updates carrying every stored bookmark, for the credentials overlay."""
    out: dict[str, str] = {}
    for cls in connector_classes:
        field = getattr(cls, "cursor_field", "")
        if field:
            value = stored(db, cls.source_name)
            if value is not None:
                out[field] = value
    return out


def advance(db: Session, connector: object) -> None:
    """Move the bookmark to what the connector read - call only after storing it."""
    value = getattr(connector, "next_cursor", None)
    if not getattr(connector, "cursor_field", "") or not value:
        return
    source = connector.source_name  # type: ignore[attr-defined]
    row = db.get(AppSetting, _key(source))
    if row is None:
        db.add(AppSetting(key=_key(source), value=str(value), updated_at=utcnow()))
    else:
        row.value = str(value)
        row.updated_at = utcnow()
    db.commit()
    log_ctx(logger, logging.INFO, "cursor advanced", source=source, cursor=str(value))
