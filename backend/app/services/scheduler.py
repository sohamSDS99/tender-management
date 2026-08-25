"""In-process scheduled fetching (APScheduler, no broker).

Two cron triggers a day at the local hours in ``SCHEDULER_HOURS_LOCAL``, in the
``SCHEDULER_TIMEZONE`` - not an interval, and not a hand-computed UTC offset.
APScheduler is given the zone itself, so "midnight in Dhaka" stays correct
without any arithmetic here.

Off unless sweeps are switched on. ``ENABLE_SCHEDULER`` is only the default for
that: an operator can pause or resume from the dashboard and the stored value
wins, applied to this process immediately (docs/DECISIONS.md D21). Exactly one
trigger owner may be enabled at a time or the same window is fetched twice; see
D2. The job body is the *same* ``run_once`` the CLI entrypoint and the GitHub
Actions workflow call, so there is one run implementation, not three.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.jobs.schedule import next_run_local
from app.logging_config import log_ctx
from app.services.schedule_settings import (
    default_enabled,
    default_hours,
    get_enabled,
    get_run_hours,
)
from app.settings import Settings, get_settings

logger = logging.getLogger(__name__)

JOB_PREFIX = "scheduled-fetch"
TRIGGER_NAME = "cron"
_scheduler: AsyncIOScheduler | None = None


def job_id(hour_local: int) -> str:
    return f"{JOB_PREFIX}-{hour_local:02d}"


def _install_jobs(scheduler: AsyncIOScheduler, hours: list[int], settings: Settings) -> None:
    """Replace the scheduler's jobs with exactly one per given local hour."""
    for job in scheduler.get_jobs():
        if job.id.startswith(JOB_PREFIX):
            job.remove()
    for hour in hours:
        scheduler.add_job(
            _job,
            trigger=CronTrigger(hour=hour, minute=0, timezone=settings.scheduler_timezone),
            id=job_id(hour),
            replace_existing=True,
            coalesce=True,
            max_instances=1,
            # A run delayed by a sleeping host still counts: the window is
            # overlapping and every upsert is idempotent, so catching up late is
            # strictly better than skipping.
            misfire_grace_time=3600,
        )


async def _job() -> None:
    """One complete run. Records trigger='cron' on every FetchRun row it creates."""
    # Imported here, not at module scope: app.jobs.scheduled_fetch imports
    # app.services.automation, which imports this module for scheduler_state().
    # At module scope that closes a cycle whose resolution depends on which file
    # a test happens to import first. The job only needs it when it fires.
    from app.jobs.scheduled_fetch import run_once

    report = await run_once(trigger=TRIGGER_NAME)
    log_ctx(
        logger,
        logging.INFO if report.exit_code == 0 else logging.ERROR,
        "scheduled fetch finished",
        batch=report.batch_id,
        created=report.created,
        slack=report.notification.get("status"),
        exit=report.exit_code,
    )


def _decision_in_force(
    settings: Settings,
    hours: list[int] | None,
    enabled: bool | None,
    session_factory: Callable[[], Session] | None = None,
) -> tuple[list[int], bool]:
    """What an operator has chosen, or the environment default.

    Both stored values win over the environment, so a change made from the
    dashboard survives a restart. A database that cannot be read must not stop
    the scheduler starting - falling back to the environment default keeps sweeps
    happening, which is the safer failure. A caller that already knows a value
    passes it in, and no session is opened for it.
    """
    if hours is not None and enabled is not None:
        return hours, enabled
    # Explicit, not the global: this function used to reach for SessionLocal
    # regardless of the Settings it was handed, so a caller passing an isolated
    # database still had its decision read from whatever the process happened to
    # be pointing at. That made the outcome depend on the developer's own
    # data/tenders.db, and two tests failed on any machine where sweeps had ever
    # been switched on from the dashboard.
    db = (session_factory or SessionLocal)()
    try:
        if enabled is None:
            enabled = get_enabled(db, settings)
        if hours is None:
            hours = get_run_hours(db, settings)
    except Exception as exc:
        # log_ctx forwards kwargs as structured context, not to the logger, so
        # the exception type goes in explicitly rather than via exc_info.
        log_ctx(
            logger,
            logging.WARNING,
            "could not read the stored trigger settings, using the environment default",
            error=type(exc).__name__,
        )
        if enabled is None:
            enabled = default_enabled(settings)
        if hours is None:
            hours = default_hours(settings)
    finally:
        db.close()
    return hours, enabled


def start_scheduler(
    settings: Settings,
    hours: list[int] | None = None,
    enabled: bool | None = None,
    session_factory: Callable[[], Session] | None = None,
) -> AsyncIOScheduler | None:
    """Idempotent: a uvicorn --reload restart replaces the jobs instead of adding.

    Needs a running event loop: ``AsyncIOScheduler.start`` binds to the loop of
    the calling thread. That is why the API's trigger endpoint is ``async def``
    and not sync - a sync route runs in a threadpool worker with no loop, and the
    scheduler would raise instead of starting.
    """
    global _scheduler
    hours, enabled = _decision_in_force(settings, hours, enabled, session_factory)
    if not enabled:
        log_ctx(logger, logging.INFO, "scheduler disabled", reason="automated sweeps are switched off")
        return None
    if _scheduler is not None:
        return _scheduler

    scheduler = AsyncIOScheduler(timezone=settings.scheduler_timezone)
    _install_jobs(scheduler, hours, settings)
    scheduler.start()
    _scheduler = scheduler
    log_ctx(
        logger,
        logging.INFO,
        "scheduler started",
        timezone=settings.scheduler_timezone,
        hours_local=",".join(str(h) for h in hours),
        next_run=next_run_local(None, tuple(hours), settings.scheduler_timezone).isoformat(),
    )
    return scheduler


def reschedule(hours: list[int], settings: Settings | None = None) -> bool:
    """Apply a new schedule to the running scheduler, without a restart.

    Returns False when no scheduler is running in this process - which is not an
    error: the operator's change is still persisted, and whichever process owns
    the schedule picks it up when it next starts.
    """
    settings = settings or get_settings()
    if _scheduler is None:
        log_ctx(logger, logging.INFO, "schedule stored but no scheduler runs here", hours=hours)
        return False
    _install_jobs(_scheduler, hours, settings)
    log_ctx(
        logger,
        logging.INFO,
        "scheduler rescheduled",
        hours=",".join(str(h) for h in hours),
        next_run=next_run_local(None, tuple(hours), settings.scheduler_timezone).isoformat(),
    )
    return True


def set_trigger(enabled: bool, hours: list[int], settings: Settings | None = None) -> bool:
    """Start or stop this process's scheduler to match a decision just stored.

    Returns whether a scheduler is running here afterwards - which is what the
    dashboard reports, and is not the same question as what was asked for. A
    resume genuinely starts one; a pause genuinely stops one, so the next sweep
    does not happen rather than happening quietly.

    Must be called from the event loop; see ``start_scheduler``.
    """
    settings = settings or get_settings()
    if not enabled:
        was_running = _scheduler is not None
        stop_scheduler()
        log_ctx(
            logger,
            logging.WARNING,
            "sweeps paused",
            stopped_a_running_scheduler=was_running,
        )
        return False
    # Pass the decision in explicitly: it has just been committed, and re-reading
    # it here would race with the transaction that wrote it.
    already_running = _scheduler is not None
    started = start_scheduler(settings, hours=hours, enabled=True)
    if started is not None and already_running:
        # start_scheduler is a no-op on an already-running scheduler, so the hours
        # have to be applied here. A fresh start installed them itself.
        _install_jobs(started, hours, settings)
    return started is not None


def scheduler_state() -> dict[str, object]:
    """What is actually scheduled in this process, not what was asked for.

    The on/off setting only records an intention. If the scheduler failed to
    start, or another trigger owns the schedule, the dashboard must be able to say
    so rather than promising a run that will never fire.
    """
    if _scheduler is None:
        return {"running": False, "jobs": []}
    jobs = []
    for job in _scheduler.get_jobs():
        next_run = job.next_run_time
        jobs.append(
            {
                "id": job.id,
                "next_run_at": (
                    next_run.astimezone(ZoneInfo("UTC")).replace(tzinfo=None) if next_run else None
                ),
            }
        )
    return {"running": bool(_scheduler.running), "jobs": jobs}


def stop_scheduler() -> None:
    """Stop the scheduler and always drop the reference.

    The reference is cleared even if the shutdown itself fails. AsyncIOScheduler
    shuts down *via* the loop it was started on (``call_soon_threadsafe``), so a
    loop that has already gone raises - and leaving ``_scheduler`` set would have
    the dashboard report a scheduler that cannot fire, which is the one thing
    scheduler_state() exists to prevent.
    """
    global _scheduler
    if _scheduler is None:
        return
    try:
        _scheduler.shutdown(wait=False)
    except (RuntimeError, AttributeError) as exc:
        log_ctx(
            logger,
            logging.WARNING,
            "scheduler could not be shut down cleanly; dropped the reference",
            error=type(exc).__name__,
        )
    finally:
        _scheduler = None
