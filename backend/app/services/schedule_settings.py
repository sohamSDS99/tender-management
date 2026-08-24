"""The sweep schedule, as a value a human can change.

The business rule stays "N times a day at these local hours in
``SCHEDULER_TIMEZONE``". What changes here is *who* decides the hours: the
environment variable is now only the default, and an operator can set them from
the dashboard.

Two things this deliberately does not do:

* it does not let the timezone be edited. Every stored datetime is naive UTC and
  Dhaka is a presentation and scheduling concern; making the zone editable from a
  web form invites a class of confusion the product has no need for.
* it does not touch the GitHub Actions cron, which is static YAML in git. When
  Actions owns the schedule, that file is authoritative and this value is not -
  see ``docs/DECISIONS.md`` D2 and D14.
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.logging_config import log_ctx
from app.models import KEY_RUN_HOURS, AppSetting, utcnow
from app.settings import Settings, get_settings

logger = logging.getLogger(__name__)

#: At least one sweep, and few enough that a mis-click cannot hammer eight public
#: APIs around the clock. A full sweep takes ~13 minutes, so six is already generous.
MIN_RUNS_PER_DAY = 1
MAX_RUNS_PER_DAY = 6


class InvalidSchedule(ValueError):
    """Raised with a message written for the person who typed it."""


def parse_hours(raw: object) -> list[int]:
    """Validate an incoming schedule. Returns sorted, de-duplicated hours.

    Rejects rather than repairs anything ambiguous: silently dropping a bad entry
    would leave the operator believing they had set something they had not.
    """
    if isinstance(raw, str):
        candidates: list[object] = [chunk.strip() for chunk in raw.split(",") if chunk.strip()]
    elif isinstance(raw, list | tuple):
        candidates = list(raw)
    else:
        raise InvalidSchedule("Provide the hours as a list, for example [0, 12].")

    hours: set[int] = set()
    for candidate in candidates:
        if isinstance(candidate, bool) or not isinstance(candidate, int | str):
            raise InvalidSchedule(f"'{candidate}' is not an hour between 0 and 23.")
        try:
            hour = int(str(candidate).strip())
        except ValueError:
            raise InvalidSchedule(f"'{candidate}' is not an hour between 0 and 23.") from None
        if not 0 <= hour <= 23:
            raise InvalidSchedule(f"{hour} is not an hour between 0 and 23.")
        hours.add(hour)

    if len(hours) < MIN_RUNS_PER_DAY:
        raise InvalidSchedule("Choose at least one time of day.")
    if len(hours) > MAX_RUNS_PER_DAY:
        raise InvalidSchedule(
            f"Choose at most {MAX_RUNS_PER_DAY} times a day. A full sweep takes about "
            "13 minutes and queries eight public services."
        )
    return sorted(hours)


def default_hours(settings: Settings | None = None) -> list[int]:
    settings = settings or get_settings()
    return settings.scheduler_hour_list or [0, 12]


def get_run_hours(db: Session, settings: Settings | None = None) -> list[int]:
    """The hours in force: the stored value, or the environment default."""
    settings = settings or get_settings()
    row = db.get(AppSetting, KEY_RUN_HOURS)
    if row is None:
        return default_hours(settings)
    try:
        return parse_hours(row.value)
    except InvalidSchedule:
        # A hand-edited row must not stop the scheduler from starting.
        log_ctx(logger, logging.WARNING, "stored schedule is invalid, using the default", value=row.value)
        return default_hours(settings)


def set_run_hours(db: Session, raw: object, settings: Settings | None = None) -> list[int]:
    """Persist a new schedule. Returns the hours actually stored."""
    settings = settings or get_settings()
    hours = parse_hours(raw)
    value = ",".join(str(h) for h in hours)
    row = db.get(AppSetting, KEY_RUN_HOURS)
    if row is None:
        db.add(AppSetting(key=KEY_RUN_HOURS, value=value, updated_at=utcnow()))
    else:
        row.value = value
        row.updated_at = utcnow()
    db.commit()
    log_ctx(logger, logging.INFO, "sweep schedule changed", hours=value, timezone=settings.scheduler_timezone)
    return hours


def is_customised(db: Session) -> bool:
    """True when an operator has set the schedule, rather than inheriting the env."""
    return db.get(AppSetting, KEY_RUN_HOURS) is not None


def reset_run_hours(db: Session) -> None:
    """Hand the schedule back to the environment default."""
    row = db.get(AppSetting, KEY_RUN_HOURS)
    if row is not None:
        db.delete(row)
        db.commit()
        log_ctx(logger, logging.INFO, "sweep schedule reset to the environment default")
