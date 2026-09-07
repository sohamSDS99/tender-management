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
from app.models import AppSetting, Source, utcnow
from app.settings import Settings

logger = logging.getLogger(__name__)

#: Source name -> the ``Settings`` field its credential overlays.
#:
#: Only sources listed here can carry a stored credential. A name that is not a
#: key here is refused rather than written, so a typo cannot create a row that
#: nothing will ever read.
CREDENTIAL_FIELDS: dict[str, str] = {
    "sam": "sam_gov_api_key",
    "highergov": "highergov_api_key",
}

#: ``Settings`` fields that may be set from the dashboard, under ``secret.{field}``.
#:
#: An explicit allow-list, not "any Settings field": without it an
#: unauthenticated page could write ``database_url`` or
#: ``allow_operator_actions``. These are the values an operator legitimately
#: rotates - a Slack app is re-issued, a channel changes - and nothing else.
#:
#: slack_channel_id is not secret, but it lives here because it is half of the
#: bot-token transport: storing one without the other leaves the app configured
#: to post nowhere, so they are set and cleared through the same door.
SETTINGS_SECRETS: tuple[str, ...] = (
    "slack_bot_token",
    "slack_channel_id",
    "slack_webhook_url",
    "slack_channel_label",
    "slack_bot_username",
    # Same reasoning as slack_channel_id: not a secret, but half of a two-part
    # configuration. HigherGov needs a key *and* a saved-search id - the API has
    # no free-text search and silently ignores unknown parameters, so a key
    # without a search_id is a connector that spends the monthly quota on the
    # unfiltered firehose. Storing one without the other is the failure mode
    # worth designing against, so they go through the same door.
    "highergov_search_id",
)

#: Which of those are true secrets, so the hint masks them.
OPAQUE_SECRETS: frozenset[str] = frozenset({"slack_bot_token", "slack_webhook_url"})


def _key(source: str) -> str:
    return f"source.{source}.credential"


def _known(db: Session, source: str) -> bool:
    """Whether this name can hold a credential at all.

    A built-in that declares a Settings field, or a source somebody added from
    the dashboard. Anything else is refused rather than written, so a typo
    cannot leave a row nothing will ever read - which is the whole point of the
    check, and why it is not simply "any string".
    """
    if source in CREDENTIAL_FIELDS:
        return True
    return db.get(Source, source) is not None


def stored_credential(db: Session, source: str) -> str | None:
    """The stored value, or None. Internal to the connector layer - see module docstring."""
    if not _known(db, source):
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
    if not _known(db, source):
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


def _secret_key(field: str) -> str:
    return f"secret.{field}"


def stored_secret(db: Session, field: str) -> str | None:
    """A stored Settings override, or None."""
    if field not in SETTINGS_SECRETS:
        return None
    row = db.get(AppSetting, _secret_key(field))
    value = (row.value or "").strip() if row else ""
    return value or None


def secret_hint(db: Session, field: str) -> str | None:
    """What is set, masked when the value is genuinely a secret."""
    value = stored_secret(db, field)
    if value is None:
        return None
    if field not in OPAQUE_SECRETS:
        return value
    return f"…{value[-4:]}" if len(value) >= 8 else "…"


def set_secret(db: Session, field: str, value: str) -> bool:
    """Store, or clear when blank. Returns False for a field that is not settable."""
    if field not in SETTINGS_SECRETS:
        log_ctx(logger, logging.WARNING, "secret refused", field=field, reason="not settable")
        return False

    cleaned = (value or "").strip()
    row = db.get(AppSetting, _secret_key(field))
    if not cleaned:
        if row is not None:
            db.delete(row)
            db.commit()
        log_ctx(logger, logging.INFO, "secret cleared", field=field)
        return True

    if row is None:
        db.add(AppSetting(key=_secret_key(field), value=cleaned, updated_at=utcnow()))
    else:
        row.value = cleaned
        row.updated_at = utcnow()
    db.commit()
    log_ctx(logger, logging.INFO, "secret set", field=field)
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
    for field in SETTINGS_SECRETS:
        value = stored_secret(db, field)
        if value is not None:
            overlay[field] = value
    return settings.model_copy(update=overlay) if overlay else settings
