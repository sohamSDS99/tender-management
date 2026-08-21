"""Rendering naive-UTC database datetimes in Dhaka local time.

Presentation only. Nothing here is ever written back to the database - every
stored datetime stays naive UTC, which is what makes the whole system
timezone-safe. See app/jobs/schedule.py for the scheduling side of the same
boundary.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from app.jobs.schedule import DHAKA

UTC = ZoneInfo("UTC")


def to_dhaka(value: datetime | None, timezone_name: str = DHAKA) -> datetime | None:
    """Interpret a naive-UTC datetime as an aware Dhaka datetime."""
    if value is None:
        return None
    aware = value.replace(tzinfo=UTC) if value.tzinfo is None else value
    return aware.astimezone(ZoneInfo(timezone_name))


def format_dhaka(value: datetime | None, with_time: bool = True, timezone_name: str = DHAKA) -> str:
    """e.g. '21 Aug 2026, 12:00'. Returns '—' for a missing datetime."""
    local = to_dhaka(value, timezone_name)
    if local is None:
        return "—"
    return local.strftime("%d %b %Y, %H:%M") if with_time else local.strftime("%d %b %Y")
