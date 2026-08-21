"""The in-process scheduler: two Dhaka cron triggers, and honest reporting."""

from __future__ import annotations

from zoneinfo import ZoneInfo

import pytest

from app.services.scheduler import job_id, scheduler_state, start_scheduler, stop_scheduler
from app.settings import Settings


@pytest.fixture(autouse=True)
async def _always_stop():
    """Shut down inside the test's own event loop.

    AsyncIOScheduler.shutdown() touches the loop it was started on. A plain
    (sync) fixture tears down after pytest-asyncio has closed that loop, which
    raises "Event loop is closed" and - worse - leaves the module-level
    _scheduler set, so the next test sees another test's jobs.
    """
    yield
    stop_scheduler()


def enabled_settings(**overrides) -> Settings:
    base = {"_env_file": None, "enable_scheduler": True, "database_url": "sqlite://"}
    base.update(overrides)
    return Settings(**base)


async def test_two_cron_triggers_are_registered_in_dhaka_time() -> None:
    scheduler = start_scheduler(enabled_settings())
    assert scheduler is not None
    jobs = {job.id: job for job in scheduler.get_jobs()}
    assert set(jobs) == {job_id(0), job_id(12)}
    for job in jobs.values():
        assert str(job.trigger).startswith("cron["), f"{job.id} is not a cron trigger"
        assert job.next_run_time.tzinfo is not None
        assert job.next_run_time.tzinfo.key == "Asia/Dhaka"
    assert {job.next_run_time.hour for job in jobs.values()} == {0, 12}


async def test_the_utc_instants_are_eighteen_hundred_and_six_hundred() -> None:
    """The same pair the GitHub Actions cron expressions encode."""
    scheduler = start_scheduler(enabled_settings())
    assert scheduler is not None
    utc_hours = {job.next_run_time.astimezone(ZoneInfo("UTC")).hour for job in scheduler.get_jobs()}
    assert utc_hours == {18, 6}


async def test_disabled_by_default_and_reports_nothing_scheduled() -> None:
    assert start_scheduler(Settings(_env_file=None, database_url="sqlite://")) is None
    assert scheduler_state() == {"running": False, "jobs": []}


async def test_starting_twice_does_not_duplicate_jobs() -> None:
    """A uvicorn --reload restart must replace the jobs, not stack them."""
    first = start_scheduler(enabled_settings())
    second = start_scheduler(enabled_settings())
    assert first is second
    assert len(first.get_jobs()) == 2


async def test_state_reports_what_is_actually_registered() -> None:
    start_scheduler(enabled_settings())
    state = scheduler_state()
    assert state["running"] is True
    jobs = state["jobs"]
    assert len(jobs) == 2
    for job in jobs:
        assert job["next_run_at"] is not None
        # Everything crossing into the database or the API is naive UTC.
        assert job["next_run_at"].tzinfo is None
        assert job["next_run_at"].hour in (18, 6)


async def test_custom_hours_are_honoured() -> None:
    scheduler = start_scheduler(enabled_settings(scheduler_hours_local="3,15,15"))
    assert {job.next_run_time.hour for job in scheduler.get_jobs()} == {3, 15}
