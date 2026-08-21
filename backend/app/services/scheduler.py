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

from app.jobs.schedule import next_run_local
from app.jobs.scheduled_fetch import run_once
from app.logging_config import log_ctx
from app.settings import Settings

logger = logging.getLogger(__name__)

JOB_PREFIX = "scheduled-fetch"
TRIGGER_NAME = "cron"
_scheduler: AsyncIOScheduler | None = None


def job_id(hour_local: int) -> str:
    return f"{JOB_PREFIX}-{hour_local:02d}"


async def _job() -> None:
    """One complete run. Records trigger='cron' on every FetchRun row it creates."""
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


def start_scheduler(settings: Settings) -> AsyncIOScheduler | None:
    """Idempotent: a uvicorn --reload restart replaces the jobs instead of adding."""
    global _scheduler
    if not settings.enable_scheduler:
        log_ctx(logger, logging.INFO, "scheduler disabled", reason="ENABLE_SCHEDULER=false")
        return None
    if _scheduler is not None:
        return _scheduler

    hours = settings.scheduler_hour_list or [0, 12]
    scheduler = AsyncIOScheduler(timezone=settings.scheduler_timezone)
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
