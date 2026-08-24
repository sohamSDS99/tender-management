"""In-process scheduled fetching (APScheduler, no broker).

Two cron triggers a day at the local hours in ``SCHEDULER_HOURS_LOCAL``, in the
``SCHEDULER_TIMEZONE`` - not an interval, and not a hand-computed UTC offset.
APScheduler is given the zone itself, so "midnight in Dhaka" stays correct
without any arithmetic here.

Off unless ``ENABLE_SCHEDULER=true``. Exactly one trigger owner may be enabled at
a time or the same window is fetched twice; see docs/DECISIONS.md (D2). The job
body is the *same* ``run_once`` the CLI entrypoint and the GitHub Actions
workflow call, so there is one run implementation, not three.
"""

from __future__ import annotations

import logging
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.db import SessionLocal
from app.jobs.schedule import next_run_local
from app.logging_config import log_ctx
from app.services.schedule_settings import default_hours, get_run_hours
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


def start_scheduler(settings: Settings, hours: list[int] | None = None) -> AsyncIOScheduler | None:
    """Idempotent: a uvicorn --reload restart replaces the jobs instead of adding."""
    global _scheduler
    if not settings.enable_scheduler:
        log_ctx(logger, logging.INFO, "scheduler disabled", reason="ENABLE_SCHEDULER=false")
        return None
    if _scheduler is not None:
        return _scheduler

    # The stored schedule wins over the environment default, so a change an
    # operator made from the dashboard survives a restart. A database that cannot
    # be read must not stop the scheduler starting - falling back to the
    # environment default keeps sweeps happening, which is the safer failure.
    if hours is None:
        db = SessionLocal()
        try:
            hours = get_run_hours(db, settings)
        except Exception as exc:
            # log_ctx forwards kwargs as structured context, not to the logger, so
            # the exception type goes in explicitly rather than via exc_info.
            log_ctx(
                logger,
                logging.WARNING,
                "could not read the stored schedule, using the environment default",
                error=type(exc).__name__,
            )
            hours = default_hours(settings)
        finally:
            db.close()

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


def scheduler_state() -> dict[str, object]:
    """What is actually scheduled in this process, not what config asked for.

    ENABLE_SCHEDULER only records an intention. If the scheduler failed to start,
    or another trigger owns the schedule, the dashboard must be able to say so
    rather than promising a run that will never fire.
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
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
