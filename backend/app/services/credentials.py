"""Source credentials an operator can set from the dashboard.

Stored in ``app_settings`` on the same rail as the sweep decision (D21): the
stored value beats the environment and applies without a restart, which is what
makes pasting a key in the browser take effect on the next sweep.

**The read path is closed, not gated.** The dashboard is unauthenticated by
design - D23 removed ``require_cron_secret()`` because the two expensive writes
are *expensive, not confidential*, and rate limits are the right control for
those. A credential inverts that property: nothing about reading a secret can
be rate-limited. So there is no function here that returns a stored value to a
caller outside the connector layer, and the API exposes only
``credential_hint``. The residual risk - someone who can reach the dashboard
replacing a key - is bounded, because a wrong key breaks that source's fetches
and surfaces immediately as a connector problem.

Connectors are deliberately untouched by any of this. ``SamGovConnector`` still
reads ``self.settings.sam_gov_api_key``; what changes is the ``Settings`` object
it is handed, via ``settings_with_stored_credentials``.
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.logging_config import log_ctx
from app.models import AppSetting, utcnow
from app.settings import Settings

logger = logging.getLogger(__name__)

#: Source name -> the ``Settings`` field its credential overlays.
#:
#: Only sources listed here can carry a stored credential. A name that is not a
#: key here is refused rather than written, so a typo cannot create a row that
#: nothing will ever read.
CREDENTIAL_FIELDS: dict[str, str] = {
    "sam": "sam_gov_api_key",
}


def _key(source: str) -> str:
    return f"source.{source}.credential"


def stored_credential(db: Session, source: str) -> str | None:
    """The stored value, or None. Internal to the connector layer - see module docstring."""
    if source not in CREDENTIAL_FIELDS:
        return None
    row = db.get(AppSetting, _key(source))
    value = (row.value or "").strip() if row else ""
    return value or None


def credential_hint(db: Session, source: str) -> str | None:
    """The last four characters, for confirming *which* key is set.

    Short values are masked entirely rather than partially: revealing three of
    four characters of a four-character secret is not a hint, it is the secret.
    """
    value = stored_credential(db, source)
    if value is None:
        return None
    return f"…{value[-4:]}" if len(value) >= 8 else "…"


def set_credential(db: Session, source: str, value: str) -> bool:
    """Store, or clear when ``value`` is blank. Returns False for an unknown source.

    Never logs the value, and the log line says only that one changed.
    """
    if source not in CREDENTIAL_FIELDS:
        log_ctx(logger, logging.WARNING, "credential refused", source=source, reason="unknown source")
        return False

    cleaned = (value or "").strip()
    row = db.get(AppSetting, _key(source))
    if not cleaned:
        if row is not None:
            db.delete(row)
            db.commit()
        log_ctx(logger, logging.INFO, "credential cleared", source=source)
        return True

    if row is None:
        db.add(AppSetting(key=_key(source), value=cleaned, updated_at=utcnow()))
    else:
        row.value = cleaned
        row.updated_at = utcnow()
    db.commit()
    log_ctx(logger, logging.INFO, "credential set", source=source)
    return True


def settings_with_stored_credentials(db: Session, settings: Settings) -> Settings:
    """A copy of ``settings`` with any stored credential overlaid.

    Returns the original object when nothing is stored, so the common path
    allocates nothing.
    """
    overlay: dict[str, str] = {}
    for source, field in CREDENTIAL_FIELDS.items():
        value = stored_credential(db, source)
        if value is not None:
            overlay[field] = value
    return settings.model_copy(update=overlay) if overlay else settings
