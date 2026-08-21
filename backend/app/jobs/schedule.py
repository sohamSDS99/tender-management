"""The one place that knows when a scheduled fetch is meant to happen.

The business rule is stated in local Dhaka time - "midnight and midday" - because
that is how the people reading the Slack digest think about it. Everything else
(the GitHub Actions cron expressions, the APScheduler triggers, the "next run"
label in the UI) is *derived* from that rule here.

Two things are deliberately not done:

* the UTC offset is never written by hand. `0 18 * * *` is computed from
  ``ZoneInfo("Asia/Dhaka")``, so the mapping is verifiable rather than asserted.
* no timezone arithmetic leaks into the database. Every stored datetime stays
  naive UTC; Dhaka time exists only at the edges (cron, presentation).
"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

DHAKA = "Asia/Dhaka"
LOCAL_RUN_HOURS: tuple[int, ...] = (0, 12)


def zone(timezone_name: str = DHAKA) -> ZoneInfo:
    return ZoneInfo(timezone_name)


def utc_offset_hours(timezone_name: str = DHAKA, at: datetime | None = None) -> float:
    """Offset from UTC in hours for ``timezone_name`` at the given local instant."""
    tz = zone(timezone_name)
    at = at or datetime.now(tz)
    if at.tzinfo is None:
        at = at.replace(tzinfo=tz)
    offset = at.utcoffset()
    assert offset is not None  # a ZoneInfo-aware datetime always has one
    return offset.total_seconds() / 3600.0


def observes_dst(timezone_name: str = DHAKA, year: int = 2026) -> bool:
    """True if the zone's UTC offset changes at any point in ``year``.

    Asia/Dhaka has been a flat UTC+6 since 2010, which is what lets a single
    fixed cron expression be correct all year. If that ever changes, the cron
    schedule becomes wrong twice a year and this returns True.
    """
    tz = zone(timezone_name)
    first = datetime(year, 1, 1, 12, tzinfo=tz).utcoffset()
    day = datetime(year, 1, 1, 12, tzinfo=tz)
    end = datetime(year + 1, 1, 1, tzinfo=tz)
    while day < end:
        if day.utcoffset() != first:
            return True
        day += timedelta(days=1)
    return False


def local_hour_to_utc(hour_local: int, timezone_name: str = DHAKA, year: int = 2026) -> tuple[int, int]:
    """Convert a local wall-clock hour to (utc_hour, day_shift).

    ``day_shift`` is -1 when the UTC instant falls on the previous calendar day,
    which is exactly what happens to 00:00 Dhaka -> 18:00 UTC the day before.
    """
    tz = zone(timezone_name)
    local = datetime.combine(datetime(year, 1, 15).date(), time(hour=hour_local), tzinfo=tz)
    utc = local.astimezone(ZoneInfo("UTC"))
    return utc.hour, (utc.date() - local.date()).days


def utc_cron_expressions(
    hours_local: tuple[int, ...] | list[int] = LOCAL_RUN_HOURS,
    timezone_name: str = DHAKA,
) -> list[str]:
    """The GitHub Actions ``schedule.cron`` values for the given local hours.

    Actions cron is UTC-only and has no timezone field, so the workflow file has
    to carry the already-converted hours. Keeping the conversion here means the
    workflow can be checked against code instead of against a comment.
    """
    out: list[str] = []
    for hour_local in hours_local:
        utc_hour, _shift = local_hour_to_utc(hour_local, timezone_name)
        out.append(f"0 {utc_hour} * * *")
    return out


def next_run_local(
    now: datetime | None = None,
    hours_local: tuple[int, ...] | list[int] = LOCAL_RUN_HOURS,
    timezone_name: str = DHAKA,
) -> datetime:
    """The next scheduled run as an aware datetime in the scheduler timezone."""
    tz = zone(timezone_name)
    now = now.astimezone(tz) if now and now.tzinfo else (now or datetime.now(tz)).replace(tzinfo=tz)
    candidates: list[datetime] = []
    for day_offset in (0, 1):
        day = (now + timedelta(days=day_offset)).date()
        for hour in sorted(hours_local):
            candidates.append(datetime.combine(day, time(hour=hour), tzinfo=tz))
    return min(c for c in candidates if c > now)


def next_run_utc(
    now: datetime | None = None,
    hours_local: tuple[int, ...] | list[int] = LOCAL_RUN_HOURS,
    timezone_name: str = DHAKA,
) -> datetime:
    """The next scheduled run as a naive UTC datetime, matching the database."""
    aware = next_run_local(now, hours_local, timezone_name)
    return aware.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)
