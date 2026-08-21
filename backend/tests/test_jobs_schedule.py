"""The Dhaka <-> UTC schedule contract.

Asia/Dhaka is UTC+6 all year, so 00:00 and 12:00 local map to 18:00 (previous
day) and 06:00 UTC. GitHub Actions cron is UTC-only with no timezone field, so
the workflow file has to carry those already-converted hours - and this test is
what stops the file and the intent from drifting apart.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from app.jobs.schedule import (
    DHAKA,
    LOCAL_RUN_HOURS,
    local_hour_to_utc,
    next_run_local,
    next_run_utc,
    observes_dst,
    utc_cron_expressions,
    utc_offset_hours,
)
from app.settings import Settings

WORKFLOW = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "scheduled-fetch.yml"


def test_dhaka_is_utc_plus_six() -> None:
    assert utc_offset_hours(DHAKA) == 6.0


def test_bangladesh_observes_no_dst() -> None:
    """A single fixed cron expression is only correct while this holds."""
    for year in (2025, 2026, 2027):
        assert observes_dst(DHAKA, year) is False


@pytest.mark.parametrize(
    ("hour_local", "expected_utc_hour", "expected_day_shift"),
    [(0, 18, -1), (12, 6, 0)],
)
def test_local_hour_maps_to_expected_utc(hour_local, expected_utc_hour, expected_day_shift) -> None:
    assert local_hour_to_utc(hour_local, DHAKA) == (expected_utc_hour, expected_day_shift)


def test_midnight_dhaka_is_eighteen_hundred_utc_the_previous_day() -> None:
    local = datetime(2026, 8, 22, 0, 0, tzinfo=ZoneInfo(DHAKA))
    utc = local.astimezone(ZoneInfo("UTC"))
    assert (utc.hour, utc.day) == (18, 21)


def test_midday_dhaka_is_six_hundred_utc_the_same_day() -> None:
    local = datetime(2026, 8, 22, 12, 0, tzinfo=ZoneInfo(DHAKA))
    utc = local.astimezone(ZoneInfo("UTC"))
    assert (utc.hour, utc.day) == (6, 22)


def test_cron_expressions_are_the_documented_pair() -> None:
    assert utc_cron_expressions(LOCAL_RUN_HOURS, DHAKA) == ["0 18 * * *", "0 6 * * *"]


def test_settings_default_to_midnight_and_midday_dhaka() -> None:
    settings = Settings(_env_file=None)
    assert settings.scheduler_timezone == DHAKA
    assert settings.scheduler_hour_list == [0, 12]


def test_scheduler_is_off_by_default() -> None:
    """Two enabled trigger owners would fetch the same window twice."""
    assert Settings(_env_file=None).enable_scheduler is False


def test_next_run_rolls_to_the_following_slot() -> None:
    tz = ZoneInfo(DHAKA)
    assert next_run_local(datetime(2026, 8, 21, 16, 4, tzinfo=tz)).hour == 0
    assert next_run_local(datetime(2026, 8, 21, 16, 4, tzinfo=tz)).day == 22
    assert next_run_local(datetime(2026, 8, 21, 6, 30, tzinfo=tz)).hour == 12
    # Exactly on the hour counts as done: the next run is the following slot.
    assert next_run_local(datetime(2026, 8, 21, 12, 0, tzinfo=tz)).hour == 0


def test_next_run_utc_is_naive_and_six_hours_behind_local() -> None:
    tz = ZoneInfo(DHAKA)
    now = datetime(2026, 8, 21, 16, 4, tzinfo=tz)
    local, utc = next_run_local(now), next_run_utc(now)
    assert utc.tzinfo is None, "the database only ever stores naive UTC"
    assert local.astimezone(ZoneInfo("UTC")).replace(tzinfo=None) == utc
    assert (local.hour - utc.hour) % 24 == 6


# --- the workflow file must agree with the code ---------------------------


def test_workflow_file_exists() -> None:
    assert WORKFLOW.is_file(), f"{WORKFLOW} is missing"


def test_workflow_cron_matches_the_computed_schedule() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    found = re.findall(r"- cron:\s*['\"]([^'\"]+)['\"]", text)
    assert sorted(found) == sorted(utc_cron_expressions(LOCAL_RUN_HOURS, DHAKA)), (
        f"workflow cron {found} does not match the Dhaka schedule "
        f"{utc_cron_expressions(LOCAL_RUN_HOURS, DHAKA)}"
    )


def test_workflow_allows_manual_replay_and_cannot_overlap() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "workflow_dispatch:" in text, "no manual replay trigger for the demo"
    assert "concurrency:" in text, "two runs could overlap"
    assert "timeout-minutes:" in text, "a hung run would burn the free minute budget"
