"""Guards for the two expensive actions the dashboard is allowed to start.

``POST /api/fetch`` and ``POST /api/tenders/rescore`` used to require
``CRON_SECRET``, which meant a browser could never call them: D5 forbids putting
a shared secret in a page, so the buttons the mockup asked for could not exist.

The gate was the wrong instrument. Reads are already completely open (D5) - these
two endpoints were never gated for *confidentiality*, they were gated because one
spends outbound requests against eight public services and the other rewrites
every stored row. That is cost control, and a secret is a poor cost control: it
says nothing about how often the action may happen, only about who may ask.

So the secret is replaced by the thing it was standing in for:

* **single-flight** - refuse while a sweep is already in flight, so the failure
  mode of a repeatedly clicked button is one sweep, not eight.
* **cooldown** - a minimum gap between operator-initiated runs, derived from data
  already recorded rather than from new state.

``CRON_SECRET`` still works and still bypasses both guards, because CI and the
scheduled entrypoint are trusted callers whose timing is already controlled.

This widens the trust boundary to "anyone on the network can start a sweep",
which is the same boundary D18/D19/D21 already accept for this accountless
internal tool. It is only defensible while the API is not reachable from the
internet - README section 12 - and ``ALLOW_OPERATOR_ACTIONS=false`` closes it.
See docs/DECISIONS.md (D23).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.logging_config import log_ctx
from app.models import KEY_LAST_RESCORE_AT, AppSetting, FetchRun, utcnow
from app.services import ingest
from app.settings import Settings

logger = logging.getLogger(__name__)

IN_FLIGHT = ("running", "queued")


def _retry_after(seconds: float) -> dict[str, str]:
    """Retry-After is what makes a 429 actionable rather than merely a refusal."""
    return {"Retry-After": str(max(1, int(seconds + 0.999)))}


def _sweep_in_flight(db: Session, settings: Settings, now: datetime) -> bool:
    """Is a sweep genuinely running right now?

    Two signals, because neither alone is enough. ``ingest.running_sources()`` is
    in-process only, so it misses a sweep started by the CLI or a prior process;
    the ``fetch_runs`` rows catch those, but a row orphaned by a crash would block
    the button for ever - so rows older than STALE_RUN_MINUTES are ignored here,
    matching what reap_interrupted_runs() would do to them anyway.
    """
    if ingest.running_sources():
        return True
    cutoff = now - timedelta(minutes=settings.stale_run_minutes)
    live = db.execute(
        select(FetchRun.id).where(FetchRun.status.in_(IN_FLIGHT), FetchRun.started_at >= cutoff).limit(1)
    ).scalar_one_or_none()
    return live is not None


def _last_sweep_started(db: Session) -> datetime | None:
    return db.execute(
        select(FetchRun.started_at).order_by(FetchRun.started_at.desc()).limit(1)
    ).scalar_one_or_none()


def guard_fetch(db: Session, settings: Settings, now: datetime | None = None) -> None:
    """Raise unless an operator may start a sweep right now.

    409 for "already running" and 429 for "too soon" are deliberately different:
    the first resolves itself and the second needs the caller to wait a stated
    number of seconds.
    """
    now = now or utcnow()
    if not settings.allow_operator_actions:
        raise HTTPException(
            status_code=403,
            detail=(
                "Starting a sweep from the dashboard is switched off "
                "(ALLOW_OPERATOR_ACTIONS=false). Use the scheduled sweep, or the CLI."
            ),
        )
    if _sweep_in_flight(db, settings, now):
        raise HTTPException(
            status_code=409,
            detail="A sweep is already running. Watch its progress below rather than starting another.",
        )
    cooldown = max(0, settings.operator_fetch_cooldown_seconds)
    last = _last_sweep_started(db)
    if cooldown and last is not None:
        elapsed = (now - last).total_seconds()
        if elapsed < cooldown:
            wait = cooldown - elapsed
            raise HTTPException(
                status_code=429,
                detail=(
                    f"A sweep ran {int(elapsed)}s ago. It queries eight public services, so "
                    f"the next one can start in {int(wait) + 1}s."
                ),
                headers=_retry_after(wait),
            )


def _last_rescore(db: Session) -> datetime | None:
    row = db.get(AppSetting, KEY_LAST_RESCORE_AT)
    if row is None:
        return None
    try:
        return datetime.fromisoformat(row.value)
    except ValueError:
        # A hand-edited row must not lock the action out for ever.
        log_ctx(logger, logging.WARNING, "unreadable last-rescore stamp, ignoring", value=row.value)
        return None


def mark_rescore(db: Session, now: datetime | None = None) -> None:
    """Record that a re-score just happened, so the cooldown has something to read."""
    now = now or utcnow()
    row = db.get(AppSetting, KEY_LAST_RESCORE_AT)
    if row is None:
        db.add(AppSetting(key=KEY_LAST_RESCORE_AT, value=now.isoformat(), updated_at=now))
    else:
        row.value = now.isoformat()
        row.updated_at = now
    db.commit()


def guard_rescore(db: Session, settings: Settings, now: datetime | None = None) -> None:
    """Raise unless an operator may re-score right now."""
    now = now or utcnow()
    if not settings.allow_operator_actions:
        raise HTTPException(
            status_code=403,
            detail=(
                "Re-scoring from the dashboard is switched off "
                "(ALLOW_OPERATOR_ACTIONS=false). Use the CLI."
            ),
        )
    cooldown = max(0, settings.operator_rescore_cooldown_seconds)
    last = _last_rescore(db)
    if cooldown and last is not None:
        elapsed = (now - last).total_seconds()
        if elapsed < cooldown:
            wait = cooldown - elapsed
            raise HTTPException(
                status_code=429,
                detail=(
                    f"Everything was re-scored {int(elapsed)}s ago. The next re-score can "
                    f"start in {int(wait) + 1}s."
                ),
                headers=_retry_after(wait),
            )
