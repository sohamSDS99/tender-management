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
from app.services.schedule_settings import (
    MAX_RUNS_PER_DAY,
    MIN_RUNS_PER_DAY,
    default_enabled,
    enabled_changed_at,
    enabled_is_customised,
    get_enabled,
    get_run_hours,
    is_customised,
)
from app.services.scheduler import scheduler_state
from app.settings import Settings, get_settings, redact

logger = logging.getLogger(__name__)

# Worst-first: the status of a batch is the worst status among its sources.
# Worst-first, but "failed" is reserved for a batch where nothing worked - see
# _batch_status.
STATUS_RANK = {"partial": 0, "running": 1, "queued": 2, "skipped": 3, "success": 4}

IN_FLIGHT = ("running", "queued")


def _batch_status(statuses: list[str]) -> str:
    """One status for a whole sweep, matching how a source reports itself.

    A single failing connector must not make the sweep read as "failed". The
    whole design point of one FetchRun per source is that one source failing does
    not fail the run: on the live sweep of 2026-08-21, pncp timed out while the
    other six sources stored 269 notices, and calling that "failed" tells the
    reader the opposite of what happened.

    So "failed" means every source failed. Some-but-not-all is "partial", which
    is exactly what an individual source calls a run that lost some records.
    """
    if not statuses:
        return "unknown"
    failed = [s for s in statuses if s == "failed"]
    if len(failed) == len(statuses):
        return "failed"
    remaining = [s for s in statuses if s != "failed"]
    if failed:
        # Something did fail; never report that as clean, even if the rest are
        # still in flight.
        return "running" if all(s in IN_FLIGHT for s in remaining) else "partial"
    return min(remaining, key=lambda s: STATUS_RANK.get(s, 99))


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
    transport = settings.slack_transport
    if not settings.enable_slack_notifications:
        return {
            "status": "disabled",
            "detail": "ENABLE_SLACK_NOTIFICATIONS is false",
            "sent_total": 0,
            "transport": transport,
        }
    if transport == "none":
        # Name both ways of fixing it. A reader who has a bot token and no
        # webhook was previously told only about the webhook.
        return {
            "status": "unconfigured",
            "detail": (
                "No Slack transport is configured. Set SLACK_BOT_TOKEN with "
                "SLACK_CHANNEL_ID, or SLACK_WEBHOOK_URL."
            ),
            "sent_total": 0,
            "transport": transport,
        }

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
            "transport": transport,
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
        "transport": transport,
    }


def automation_status(
    db: Session, settings: Settings | None = None, now: datetime | None = None
) -> dict[str, Any]:
    """The full read-only automation picture for GET /api/automation."""
    settings = settings or get_settings()
    now = now or utcnow()
    # What is actually in force, which may be an operator's choice rather than
    # the environment default.
    hours = tuple(get_run_hours(db, settings))
    enabled = get_enabled(db, settings)
    tz = settings.scheduler_timezone

    state = scheduler_state()
    batch = last_batch(db)
    statuses = [r.status for r in batch]
    finished = [r.finished_at for r in batch if r.finished_at]
    batch_id = next((r.batch_id for r in batch if r.batch_id), None)

    return {
        # Every Slack digest links to this base. A wrong value means every link
        # is dead and nothing on screen would otherwise say so.
        "public_app_url": settings.app_base_url,
        "timezone": tz,
        "run_hours_local": list(hours),
        "run_hours_are_custom": is_customised(db),
        "run_hours_min": MIN_RUNS_PER_DAY,
        "run_hours_max": MAX_RUNS_PER_DAY,
        # The dashboard seeds its depth control from this rather than keeping its
        # own copy, for the same reason the score bands come from /api/stats: two
        # copies of a number drift, and this one decides whether the Fetch button
        # searches a window the scheduler has already emptied.
        "operator_fetch_days_back": settings.operator_fetch_days_back,
        "cron_utc": utc_cron_expressions(hours, tz),
        "observes_dst": observes_dst(tz, now.year),
        "next_run_at": next_run_utc(None, hours, tz),
        "next_run_local_label": next_run_local(None, hours, tz).strftime("%d %b %Y, %H:%M"),
        # Intent vs reality: enabled-but-not-running is a real failure mode and
        # has to be visible, not hidden behind the config flag. The intent is the
        # decision in force - an operator's pause, or the environment default -
        # and not ENABLE_SCHEDULER alone, or pausing would read as that failure.
        "scheduler_in_process": enabled,
        "scheduler_running": bool(state["running"]),
        "scheduler_jobs": state["jobs"],
        # Enough for the dashboard to say whose decision this is, and to offer
        # handing it back. There is no *who*: the product has no accounts (D18).
        "trigger_is_custom": enabled_is_customised(db),
        "trigger_default": default_enabled(settings),
        "trigger_changed_at": enabled_changed_at(db),
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
