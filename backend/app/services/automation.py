"""What the dashboard shows where the manual fetch buttons used to be.

The UI has no way to start a fetch any more, so it has to answer three questions
instead: when is the next run, how did the last one go, and is anything broken.
All three are read-only projections over ``fetch_runs`` and
``slack_notifications`` - this module never triggers anything.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.jobs.schedule import next_run_local, next_run_utc, observes_dst, utc_cron_expressions
from app.logging_config import log_ctx
from app.models import SENT, UNCONFIRMED, FetchRun, SlackNotification, utcnow
from app.services.dhaka import format_dhaka
from app.services.scheduler import scheduler_state
from app.settings import Settings, get_settings, redact

logger = logging.getLogger(__name__)

# Worst-first: the status of a batch is the worst status among its sources.
STATUS_RANK = {"failed": 0, "partial": 1, "running": 2, "queued": 3, "skipped": 4, "success": 5}


def _batch_status(statuses: list[str]) -> str:
    if not statuses:
        return "unknown"
    return min(statuses, key=lambda s: STATUS_RANK.get(s, 99))


def reap_interrupted_runs(db: Session, settings: Settings | None = None, now: datetime | None = None) -> int:
    """Close out runs that a restart orphaned. Returns how many were reaped.

    A run executes in-process, so it cannot survive the process dying: rows left
    at running/queued would otherwise sit there for ever and the dashboard would
    keep reporting "running". Only rows older than STALE_RUN_MINUTES are touched,
    so a genuinely in-flight run started by another process (an operator using
    the CLI while the API restarts) is never mistaken for an orphan.
    """
    settings = settings or get_settings()
    now = now or utcnow()
    cutoff = now - timedelta(minutes=settings.stale_run_minutes)
    rows = (
        db.execute(
            select(FetchRun).where(
                FetchRun.status.in_(("running", "queued")),
                FetchRun.started_at < cutoff,
            )
        )
        .scalars()
        .all()
    )
    for row in rows:
        row.status = "failed"
        row.finished_at = now
        row.error_message = (
            "Interrupted: the process running this fetch stopped before it finished. "
            "Ingested notices were committed as they arrived; re-run to pick up the rest."
        )
    if rows:
        db.commit()
        log_ctx(logger, logging.WARNING, "reaped interrupted runs", runs=len(rows))
    return len(rows)


def last_batch(db: Session) -> list[FetchRun]:
    """Every FetchRun row belonging to the most recent sweep.

    Prefers ``batch_id``; falls back to the identical ``window_to`` shared by one
    sweep's rows, so runs recorded before that column existed still group.
    """
    latest = db.execute(
        select(FetchRun).order_by(FetchRun.started_at.desc(), FetchRun.id.desc()).limit(1)
    ).scalar_one_or_none()
    if latest is None:
        return []
    if latest.batch_id:
        stmt = select(FetchRun).where(FetchRun.batch_id == latest.batch_id)
    elif latest.window_to is not None:
        stmt = select(FetchRun).where(FetchRun.window_to == latest.window_to)
    else:
        stmt = select(FetchRun).where(FetchRun.id == latest.id)
    return list(db.execute(stmt.order_by(FetchRun.source)).scalars().all())


def slack_state(
    db: Session, settings: Settings, batch_id: str | None, now: datetime | None = None
) -> dict[str, Any]:
    """Slack health. A failed delivery is visible here, never swallowed."""
    now = now or utcnow()
    if not settings.enable_slack_notifications:
        return {"status": "disabled", "detail": "ENABLE_SLACK_NOTIFICATIONS is false", "sent_total": 0}
    if not settings.slack_webhook_url:
        return {"status": "unconfigured", "detail": "SLACK_WEBHOOK_URL is not set", "sent_total": 0}

    sent_total = db.execute(
        select(func.count(SlackNotification.id)).where(SlackNotification.status == SENT)
    ).scalar_one()
    latest_failure = db.execute(
        select(SlackNotification)
        .where(SlackNotification.status == "failed")
        .order_by(SlackNotification.claimed_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    # A delivery we could not confirm needs a human to look at the channel: it
    # is deliberately never retried, because retrying could post it twice.
    unconfirmed = db.execute(
        select(func.count(SlackNotification.id)).where(SlackNotification.status == UNCONFIRMED)
    ).scalar_one()
    sent_in_batch = (
        db.execute(
            select(func.count(SlackNotification.id)).where(
                SlackNotification.run_batch_id == batch_id, SlackNotification.status == SENT
            )
        ).scalar_one()
        if batch_id
        else 0
    )

    # A failure only counts as *current* if it belongs to the last sweep, or is
    # recent enough that no run has had a chance to retry it yet. Otherwise a
    # single old failure would show the system as degraded for ever, including
    # after later runs have succeeded.
    recent_enough = latest_failure is not None and latest_failure.claimed_at >= now - timedelta(
        hours=max(1, settings.slack_announce_lookback_hours)
    )
    degraded = latest_failure is not None and (
        latest_failure.run_batch_id == batch_id if batch_id else recent_enough
    )
    if unconfirmed:
        return {
            "status": "unconfirmed",
            "detail": (
                f"{unconfirmed} announcement(s) left this system but Slack never confirmed "
                "receipt. They are not retried automatically, because Slack's incoming "
                "webhooks have no idempotency key and a retry could post them twice. Check "
                "the channel: see docs/RUNBOOK.md section 4."
            ),
            "sent_total": sent_total,
            "unconfirmed": unconfirmed,
            "sent_in_last_batch": sent_in_batch,
            "channel_label": settings.slack_channel_label,
            "min_score": settings.slack_min_score,
        }
    return {
        "status": "degraded" if degraded else "ok",
        "detail": redact(latest_failure.error_message or "delivery failed", settings)
        if degraded and latest_failure
        else None,
        "sent_total": sent_total,
        "unconfirmed": 0,
        "sent_in_last_batch": sent_in_batch,
        "channel_label": settings.slack_channel_label,
        "min_score": settings.slack_min_score,
    }


def automation_status(
    db: Session, settings: Settings | None = None, now: datetime | None = None
) -> dict[str, Any]:
    """The full read-only automation picture for GET /api/automation."""
    settings = settings or get_settings()
    now = now or utcnow()
    hours = tuple(settings.scheduler_hour_list)
    tz = settings.scheduler_timezone

    state = scheduler_state()
    batch = last_batch(db)
    statuses = [r.status for r in batch]
    finished = [r.finished_at for r in batch if r.finished_at]
    batch_id = next((r.batch_id for r in batch if r.batch_id), None)

    return {
        "timezone": tz,
        "run_hours_local": list(hours),
        "cron_utc": utc_cron_expressions(hours, tz),
        "observes_dst": observes_dst(tz, now.year),
        "next_run_at": next_run_utc(None, hours, tz),
        "next_run_local_label": next_run_local(None, hours, tz).strftime("%d %b %Y, %H:%M"),
        # Intent vs reality: enabled-but-not-running is a real failure mode and
        # has to be visible, not hidden behind the config flag.
        "scheduler_in_process": settings.enable_scheduler,
        "scheduler_running": bool(state["running"]),
        "scheduler_jobs": state["jobs"],
        "last_run": (
            {
                "batch_id": batch_id,
                "trigger": batch[0].trigger,
                "status": _batch_status(statuses),
                "started_at": min(r.started_at for r in batch),
                "finished_at": max(finished) if finished else None,
                "started_at_local_label": format_dhaka(min(r.started_at for r in batch), timezone_name=tz),
                "sources_total": len(batch),
                "sources_failed": sum(1 for s in statuses if s == "failed"),
                "records_received": sum(r.records_received for r in batch),
                "records_created": sum(r.records_created for r in batch),
                "records_updated": sum(r.records_updated for r in batch),
                "errors": [
                    {"source": r.source, "message": redact(r.error_message, settings)[:300]}
                    for r in batch
                    if r.error_message
                ],
            }
            if batch
            else None
        ),
        "slack": slack_state(db, settings, batch_id, now),
    }
