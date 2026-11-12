"""When Tender Monitor breaks, email a named owner (D43).

Seven checks read the tables the system already writes. ``evaluate`` turns
their answers into alarm rows and emails:

    new problem          -> open an alarm, send ALARM (To owner, CC team)
    still open, unsent   -> try the ALARM email again
    open > N office hrs  -> one REMINDER, everyone in To
    problem gone         -> close it, send FIXED

Nobody clicks anything: an alarm closes when the data shows the system works
again, so the "fixed" time is proven by the data rather than claimed. There are
no links to act on in the email either - every route is behind sign-in (D26),
and mail scanners follow links.

A check returns a list of current problems, or ``None`` when it cannot tell
right now (a sweep is in flight, sweeps are paused): its open alarms are then
left exactly as they are, neither re-sent nor closed.

Nothing in this module may break a sweep. ``evaluate_safely`` and
``record_crash`` never raise, and every caller wraps them anyway.
"""

from __future__ import annotations

import asyncio
import html
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.logging_config import log_ctx
from app.models import KEY_RUN_HOURS, Alarm, AppSetting, FetchRun, SlackNotification, utcnow
from app.services import mailer
from app.services.automation import IN_FLIGHT, _batch_status, last_batch
from app.services.schedule_settings import enabled_changed_at, get_enabled, get_run_hours
from app.settings import Settings, get_settings, redact

logger = logging.getLogger(__name__)

UTC = ZoneInfo("UTC")

#: The scheduler lets a sweep start up to 60 minutes late (misfire_grace_time),
#: plus 15 minutes of margin.
MISSED_AFTER = timedelta(minutes=75)

SWEEP_CRASHED = "sweep_crashed"
ALL_SOURCES_FAILED = "all_sources_failed"
SWEEP_MISSED = "sweep_missed"
SOURCE_FAILING = "source_failing"
SLACK_UNDELIVERED = "slack_undelivered"
NO_DATA = "no_data"
SWEEPS_PAUSED = "sweeps_paused"


@dataclass(frozen=True)
class Problem:
    check_key: str
    subject: str
    title: str
    detail: str


# --- the office-hours clock --------------------------------------------------


def _local(value: datetime, settings: Settings) -> datetime:
    return value.replace(tzinfo=UTC).astimezone(ZoneInfo(settings.scheduler_timezone))


def _naive_utc(value: datetime) -> datetime:
    return value.astimezone(UTC).replace(tzinfo=None)


def _office(day: date, settings: Settings) -> tuple[datetime, datetime] | None:
    """That day's office window in local time, or None on a day off."""
    if day.weekday() not in settings.alarm_workday_numbers:
        return None
    start, end = settings.alarm_work_window
    midnight = datetime.combine(day, time(0), ZoneInfo(settings.scheduler_timezone))
    return midnight + timedelta(hours=start), midnight + timedelta(hours=end)


def working_day_hours(settings: Settings) -> float:
    start, end = settings.alarm_work_window
    return float(end - start)


def working_hours_between(start: datetime, end: datetime, settings: Settings) -> float:
    """Office hours between two naive-UTC times, counted in the scheduler zone."""
    if end <= start:
        return 0.0
    s, e = _local(start, settings), _local(end, settings)
    total = 0.0
    day = s.date()
    while day <= e.date():  # an alarm is open for days, not years
        window = _office(day, settings)
        if window:
            total += max((min(window[1], e) - max(window[0], s)).total_seconds(), 0.0)
        day += timedelta(days=1)
    return total / 3600


def add_working_hours(start: datetime, hours: float, settings: Settings) -> datetime:
    """The naive-UTC moment ``hours`` office hours after ``start``."""
    s = _local(start, settings)
    remaining = timedelta(hours=hours)
    day = s.date()
    for _ in range(400):
        window = _office(day, settings)
        if window:
            begin = max(window[0], s)
            if begin < window[1]:
                if window[1] - begin >= remaining:
                    return _naive_utc(begin + remaining)
                remaining -= window[1] - begin
        day += timedelta(days=1)
    raise ValueError("ALARM_WORKDAYS / ALARM_WORK_HOURS leave no office hours")


# --- reading the tables ------------------------------------------------------


def _open_alarm(db: Session, check_key: str, subject: str = "") -> Alarm | None:
    return db.execute(
        select(Alarm).where(
            Alarm.check_key == check_key, Alarm.subject == subject, Alarm.resolved_at.is_(None)
        )
    ).scalar_one_or_none()


def _sweep_finished_since(db: Session, since: datetime) -> bool:
    """True when a sweep started at or after ``since`` and all its sources finished."""
    done: dict[str, bool] = {}
    rows = db.execute(
        select(FetchRun.id, FetchRun.batch_id, FetchRun.finished_at).where(FetchRun.started_at >= since)
    ).all()
    for row_id, batch_id, finished_at in rows:
        key = batch_id or f"run-{row_id}"
        done[key] = done.get(key, True) and finished_at is not None
    return any(done.values())


def _last_sweep_line(db: Session | None, settings: Settings) -> str:
    if db is None:
        return "unknown — the database could not be read"
    rows = last_batch(db)
    if not rows:
        return "none recorded"
    status = _batch_status([r.status for r in rows])
    outcome = {
        "success": "finished OK",
        "partial": "finished, some sources failed",
        "failed": "every source failed",
        "skipped": "finished, nothing to do",
    }.get(status, status)
    return f"{_when(min(r.started_at for r in rows), settings)} (Dhaka) — {outcome}"


# --- the seven checks --------------------------------------------------------


def check_sweep_crashed(db: Session, settings: Settings, now: datetime) -> list[Problem] | None:
    """1. Opened by ``record_crash``; holds until a later sweep finishes."""
    alarm = _open_alarm(db, SWEEP_CRASHED)
    if alarm is None or _sweep_finished_since(db, alarm.opened_at):
        return []
    return [Problem(SWEEP_CRASHED, "", alarm.title, alarm.detail)]


def check_all_sources_failed(db: Session, settings: Settings, now: datetime) -> list[Problem] | None:
    """2. The newest sweep finished and not one source worked.

    Only a sweep of two or more sources counts: an operator fetching one source
    that fails is check 4's business, and "every source failed" would overstate it.
    """
    rows = last_batch(db)
    if any(r.status in IN_FLIGHT for r in rows):
        return None
    if len(rows) < 2 or any(r.status != "failed" for r in rows):
        return []
    reasons = "\n".join(f"{r.source}: {redact((r.error_message or 'failed')[:160], settings)}" for r in rows)
    return [
        Problem(
            ALL_SOURCES_FAILED,
            "",
            "every source failed in the last sweep",
            f"The sweep that started {_when(min(r.started_at for r in rows), settings)} (Dhaka) "
            f"ran {len(rows)} sources and every one failed. No notices were stored.\n"
            f"Usual causes: no network from the server, or a timeout set too low.\n\n{reasons}",
        )
    ]


def check_sweep_missed(db: Session, settings: Settings, now: datetime) -> list[Problem] | None:
    """3. A sweep was due and none started and finished within 75 minutes."""
    always = settings.alarm_expect_sweeps == "always"
    if not always and not get_enabled(db, settings):
        return None  # paused on purpose: check 7's business, and nothing to close
    if _open_alarm(db, SWEEP_CRASHED) is not None:
        return None  # the sweep did start; check 1 has already said so
    # The newest due time that is at least MISSED_AFTER old, today or yesterday.
    judged = _local(now - MISSED_AFTER, settings)
    midnight = judged.replace(hour=0, minute=0, second=0, microsecond=0)
    slots = [
        midnight - timedelta(days=back, hours=-h) for back in (0, 1) for h in get_run_hours(db, settings)
    ]
    past = [slot for slot in slots if slot <= judged]
    if not past:
        return []
    due = _naive_utc(max(past))
    # A slot from before sweeps were switched on, or before the hours changed,
    # was never owed.
    hours_row = db.get(AppSetting, KEY_RUN_HOURS)
    changes = [hours_row.updated_at if hours_row else None, None if always else enabled_changed_at(db)]
    if any(changed is not None and changed > due for changed in changes):
        return []
    if _sweep_finished_since(db, due):
        return []
    slot = _local(due, settings).strftime("%H:%M")
    switched = (
        "ALARM_EXPECT_SWEEPS=always, so sweeps are expected whatever the dashboard says."
        if always
        else "Sweeps are switched ON in the dashboard."
    )
    return [
        Problem(
            SWEEP_MISSED,
            "",
            f"the {slot} sweep did not run",
            f"A sweep was due at {slot} Dhaka and none had started and finished by "
            f"{_local(due + MISSED_AFTER, settings).strftime('%H:%M')}.\n{switched}\n"
            "Usual causes: the scheduler did not start, or a sweep is stuck.",
        )
    ]


def check_source_failing(db: Session, settings: Settings, now: datetime) -> list[Problem] | None:
    """4. The same source failed in its last N finished runs."""
    streak = settings.alarm_source_fail_streak
    rows = db.execute(
        select(FetchRun.source, FetchRun.status, FetchRun.error_message)
        .where(FetchRun.status.not_in(IN_FLIGHT), FetchRun.started_at >= now - timedelta(days=30))
        .order_by(FetchRun.started_at.desc(), FetchRun.id.desc())
    ).all()
    recent: dict[str, list[tuple[str, str | None]]] = {}
    for source, status, error in rows:
        if len(recent.setdefault(source, [])) < streak:
            recent[source].append((status, error))
    problems = []
    for source, runs in sorted(recent.items()):
        if len(runs) == streak and all(status == "failed" for status, _ in runs):
            last_error = redact((runs[0][1] or "no error recorded")[:300], settings)
            problems.append(
                Problem(
                    SOURCE_FAILING,
                    source,
                    f"{source} failed {streak} sweeps in a row",
                    f"The source '{source}' failed in each of its last {streak} runs. "
                    f"The other sources are not affected.\n\nLast error: {last_error}",
                )
            )
    return problems


def check_slack_undelivered(db: Session, settings: Settings, now: datetime) -> list[Problem] | None:
    """5. The newest digest left tenders failed or unconfirmed in the ledger."""
    batch = db.execute(
        select(SlackNotification.run_batch_id).order_by(SlackNotification.claimed_at.desc()).limit(1)
    ).scalar_one_or_none()
    if batch is None:
        return []
    statuses = db.execute(
        select(SlackNotification.status, func.count())
        .where(SlackNotification.run_batch_id == batch)
        .group_by(SlackNotification.status)
    ).all()
    bad = {status: count for status, count in statuses if status in ("failed", "unconfirmed")}
    if not bad:
        return []
    counts = ", ".join(f"{count} {status}" for status, count in sorted(bad.items()))
    return [
        Problem(
            SLACK_UNDELIVERED,
            "",
            "the Slack digest was not delivered",
            f"New tenders were saved, but the Slack digest for sweep {batch} was not delivered ({counts}).\n"
            "Failed posts are retried by the next sweep. Unconfirmed ones are never retried, "
            "so nothing is posted twice: check the channel (docs/RUNBOOK.md section 4).",
        )
    ]


def check_no_data(db: Session, settings: Settings, now: datetime) -> list[Problem] | None:
    """6. Sweeps ran over the last N days and received nothing at all."""
    since = now - timedelta(days=settings.alarm_no_data_days)
    oldest = db.execute(select(func.min(FetchRun.started_at))).scalar_one_or_none()
    if oldest is None or oldest > since:
        return []  # not running long enough to judge
    runs, received = db.execute(
        select(func.count(FetchRun.id), func.coalesce(func.sum(FetchRun.records_received), 0)).where(
            FetchRun.started_at >= since, FetchRun.finished_at.is_not(None)
        )
    ).one()
    if runs == 0 or received > 0:
        return []  # no runs at all is check 3's business
    return [
        Problem(
            NO_DATA,
            "",
            f"nothing received in {settings.alarm_no_data_days} days",
            f"{runs} source runs finished in the last {settings.alarm_no_data_days} days and "
            "received 0 notices between them. The sweeps run, but every source came back empty.\n"
            "Usual causes: a changed source API, a broken prefilter, or a date window that is wrong.",
        )
    ]


def check_sweeps_paused(db: Session, settings: Settings, now: datetime) -> list[Problem] | None:
    """7. Sweeps were paused from the dashboard more than one working day ago."""
    if settings.alarm_expect_sweeps == "always" or get_enabled(db, settings):
        return []
    paused_at = enabled_changed_at(db)
    if paused_at is None:
        return []  # off by environment default: a configuration, not a pause
    if working_hours_between(paused_at, now, settings) <= working_day_hours(settings):
        return []
    return [
        Problem(
            SWEEPS_PAUSED,
            "",
            "sweeps have been switched off for more than one working day",
            f"Automated sweeps were paused from the dashboard on {_when(paused_at, settings)} (Dhaka) "
            "and are still off. No new tenders are being fetched.\n"
            "If that is deliberate, ignore this; it closes when sweeps are switched back on.",
        )
    ]


CHECKS: tuple[tuple[str, Callable[[Session, Settings, datetime], list[Problem] | None]], ...] = (
    (SWEEP_CRASHED, check_sweep_crashed),
    (ALL_SOURCES_FAILED, check_all_sources_failed),
    (SWEEP_MISSED, check_sweep_missed),
    (SOURCE_FAILING, check_source_failing),
    (SLACK_UNDELIVERED, check_slack_undelivered),
    (NO_DATA, check_no_data),
    (SWEEPS_PAUSED, check_sweeps_paused),
)


# --- the email ---------------------------------------------------------------


def _when(value: datetime, settings: Settings) -> str:
    return _local(value, settings).strftime("%a %d %b %H:%M")


def _duration(start: datetime, end: datetime) -> str:
    minutes = int((end - start).total_seconds() // 60)
    days, minutes = divmod(minutes, 24 * 60)
    hours, minutes = divmod(minutes, 60)
    parts = ([f"{days} d"] if days else []) + ([f"{hours} h"] if hours or days else []) + [f"{minutes} min"]
    return " ".join(parts)


def _office_label(settings: Settings) -> str:
    names = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
    days = sorted(settings.alarm_workday_numbers)
    span = (
        f"{names[days[0]]}–{names[days[-1]]}"
        if days == list(range(days[0], days[-1] + 1))
        else ",".join(names[d] for d in days)
    )
    start, end = settings.alarm_work_window
    return f"{span} {start:02d}:00–{end:02d}:00"


@dataclass(frozen=True)
class Email:
    subject: str
    text: str
    to: list[str]
    cc: list[str]

    @property
    def html(self) -> str:
        return (
            f'<pre style="font-family:ui-monospace,Menlo,Consolas,monospace">{html.escape(self.text)}</pre>'
        )


def _footer(settings: Settings) -> str:
    return (
        "How to check\n"
        f"  Dashboard: {settings.app_base_url}/   (Automation panel)\n"
        "  Logs:      Railway → Tender-Management → backend\n\n"
        "This alarm closes by itself when the data shows the system works again.\n"
        "There is nothing to click."
    )


def _why(detail: str) -> str:
    return "Why\n" + "\n".join(f"  {line}" if line else "" for line in detail.splitlines())


def alarm_email(alarm: Alarm, db: Session | None, settings: Settings) -> Email:
    owner = settings.alarm_owner_name
    day = working_day_hours(settings)
    fix_by = add_working_hours(alarm.opened_at, day, settings)
    text = (
        f"What broke   {alarm.title[:1].upper()}{alarm.title[1:]}\n"
        f"Opened       {_when(alarm.opened_at, settings)} (Dhaka)\n"
        f"Last sweep   {_last_sweep_line(db, settings)}\n"
        f"Owner        {owner} — please fix within one working day\n"
        f"Fix by       {_when(fix_by, settings)} (Dhaka) — {day:g} office hours ({_office_label(settings)})\n\n"
        f"{_why(alarm.detail)}\n\n{_footer(settings)}\n"
    )
    return Email(
        f"[ALARM] Tender Monitor — {alarm.title} — owner: {owner}",
        text,
        settings.alarm_to_list,
        settings.alarm_cc_list,
    )


def reminder_email(alarm: Alarm, db: Session, settings: Settings, now: datetime) -> Email:
    owner = settings.alarm_owner_name
    open_for = working_hours_between(alarm.opened_at, now, settings)
    fix_by = add_working_hours(alarm.opened_at, working_day_hours(settings), settings)
    text = (
        f"Still broken after {open_for:.1f} office hours.\n\n"
        f"What broke   {alarm.title[:1].upper()}{alarm.title[1:]}\n"
        f"Opened       {_when(alarm.opened_at, settings)} (Dhaka)\n"
        f"Last sweep   {_last_sweep_line(db, settings)}\n"
        f"Owner        {owner}\n"
        f"Fix by       {_when(fix_by, settings)} (Dhaka)\n\n"
        f"{_why(alarm.detail)}\n\n{_footer(settings)}\n"
    )
    return Email(
        f"[REMINDER] Tender Monitor — {alarm.title} — owner: {owner}",
        text,
        settings.alarm_to_list + settings.alarm_cc_list,
        [],
    )


def fixed_email(alarm: Alarm, db: Session, settings: Settings) -> Email:
    owner = settings.alarm_owner_name
    assert alarm.resolved_at is not None
    office = working_hours_between(alarm.opened_at, alarm.resolved_at, settings)
    within = office <= working_day_hours(settings)
    verdict = "within one working day" if within else "longer than one working day"
    text = (
        f"Fixed        {alarm.title[:1].upper()}{alarm.title[1:]}\n"
        f"Opened       {_when(alarm.opened_at, settings)} (Dhaka)\n"
        f"Closed       {_when(alarm.resolved_at, settings)} (Dhaka)\n"
        f"Open for     {_duration(alarm.opened_at, alarm.resolved_at)} "
        f"({office:.1f} office hours, {verdict})\n"
        f"Last sweep   {_last_sweep_line(db, settings)}\n"
        f"Owner        {owner}\n\n"
        "The data shows it works again, so the alarm closed itself.\n"
    )
    return Email(
        f"[FIXED] Tender Monitor — {alarm.title} — owner: {owner}",
        text,
        settings.alarm_to_list,
        settings.alarm_cc_list,
    )


# --- open, remind, close -----------------------------------------------------


def _claim(db: Session, alarm: Alarm, column: str, now: datetime) -> bool:
    """Stamp ``column`` only if it is still NULL. Whoever wins sends the email.

    The sweep's own check and the watchdog can run at the same moment; without
    this both would read "not sent yet" and both would send.
    """
    col = getattr(Alarm, column)
    won = db.execute(update(Alarm).where(Alarm.id == alarm.id, col.is_(None)).values({column: now})).rowcount
    db.commit()
    return won == 1


def _release(db: Session, alarm: Alarm, column: str) -> None:
    """Brevo refused or was unreachable: un-stamp so the next tick retries."""
    db.execute(update(Alarm).where(Alarm.id == alarm.id).values({column: None}))
    db.commit()


def _deliver(db: Session, alarm: Alarm, column: str, email: Email, settings: Settings, now: datetime) -> bool:
    if not _claim(db, alarm, column, now):
        return False
    try:
        sent = mailer.send(email.subject, email.text, email.html, to=email.to, cc=email.cc, settings=settings)
    except Exception as exc:  # send() promises not to raise; a claim must never outlive a lie
        log_ctx(logger, logging.ERROR, "email failed", subject=email.subject, error=type(exc).__name__)
        sent = False
    if not sent:
        _release(db, alarm, column)
    return sent


def _open(db: Session, problem: Problem, now: datetime) -> None:
    db.add(
        Alarm(
            check_key=problem.check_key,
            subject=problem.subject,
            title=problem.title[:255],
            detail=problem.detail,
            opened_at=now,
        )
    )
    try:
        db.commit()
    except IntegrityError:
        db.rollback()  # another evaluator opened it a moment ago; theirs stands
        return
    log_ctx(logger, logging.ERROR, "alarm opened", check=problem.check_key, subject=problem.subject)


def evaluate(db: Session, settings: Settings, now: datetime | None = None) -> None:
    """Run every check, open and close alarms, and send what is owed."""
    if not settings.alarms_enabled:
        return
    now = now or utcnow()
    for check_key, check in CHECKS:
        try:
            problems = check(db, settings, now)
        except Exception as exc:
            db.rollback()
            log_ctx(logger, logging.ERROR, "alarm check failed", check=check_key, error=type(exc).__name__)
            continue
        if problems is None:
            continue
        current = {p.subject: p for p in problems}
        open_rows = db.execute(
            select(Alarm).where(Alarm.check_key == check_key, Alarm.resolved_at.is_(None))
        ).scalars()
        for alarm in list(open_rows):
            if current.pop(alarm.subject, None) is None:
                alarm.resolved_at = now
                db.commit()
                log_ctx(
                    logger,
                    logging.INFO,
                    "alarm resolved",
                    check=check_key,
                    subject=alarm.subject,
                    open_for=_duration(alarm.opened_at, now),
                )
        for problem in current.values():
            _open(db, problem, now)

    open_alarms = (
        db.execute(select(Alarm).where(Alarm.resolved_at.is_(None)).order_by(Alarm.id)).scalars().all()
    )
    for alarm in open_alarms:
        if alarm.emailed_at is None:
            _deliver(db, alarm, "emailed_at", alarm_email(alarm, db, settings), settings, now)
        elif (
            alarm.reminded_at is None
            and working_hours_between(alarm.opened_at, now, settings) >= settings.alarm_reminder_working_hours
        ):
            _deliver(db, alarm, "reminded_at", reminder_email(alarm, db, settings, now), settings, now)

    # A FIXED email is owed only for an alarm whose ALARM email went out.
    owed = db.execute(
        select(Alarm)
        .where(
            Alarm.resolved_at.is_not(None), Alarm.emailed_at.is_not(None), Alarm.fixed_emailed_at.is_(None)
        )
        .order_by(Alarm.id)
    ).scalars()
    for alarm in list(owed):
        _deliver(db, alarm, "fixed_emailed_at", fixed_email(alarm, db, settings), settings, now)


def evaluate_safely(session_factory: Callable[[], Session], settings: Settings) -> None:
    """``evaluate`` in its own session. Never raises: a sweep calls this."""
    try:
        db = session_factory()
        try:
            evaluate(db, settings)
        finally:
            db.close()
    except Exception as exc:
        log_ctx(logger, logging.ERROR, "alarm evaluation failed", error=type(exc).__name__)


def record_crash(exc: BaseException, session_factory: Callable[[], Session], settings: Settings) -> None:
    """Open alarm 1 for a sweep that raised, and email it now. Never raises.

    If the database is what failed, there is nowhere to record the alarm, so the
    email is sent straight away instead; there will be no FIXED email for it.
    """
    if not settings.alarms_enabled:
        return
    problem = Problem(
        SWEEP_CRASHED,
        "",
        "the sweep crashed",
        f"A sweep stopped with an error: {type(exc).__name__}: {redact(str(exc)[:500], settings)}\n"
        "Notices stored before the crash are kept. The next sweep retries the rest.\n"
        "Usual causes: the database went away mid-run, or a code error.",
    )
    try:
        db = session_factory()
        try:
            if _open_alarm(db, SWEEP_CRASHED) is None:
                _open(db, problem, utcnow())
            evaluate(db, settings)
        finally:
            db.close()
    except Exception as db_exc:
        log_ctx(logger, logging.ERROR, "could not record the crash alarm", error=type(db_exc).__name__)
        try:
            now = utcnow()
            email = alarm_email(
                Alarm(title=problem.title, detail=problem.detail, opened_at=now), None, settings
            )
            mailer.send(email.subject, email.text, email.html, to=email.to, cc=email.cc, settings=settings)
        except Exception as mail_exc:
            log_ctx(logger, logging.ERROR, "could not send the crash alarm", error=type(mail_exc).__name__)


# --- reading them back ---------------------------------------------------------

RECENT_LIMIT = 20


def overview(db: Session, settings: Settings, now: datetime | None = None) -> dict[str, object]:
    """Open alarms, the most recently closed, and the 30-day fix rate."""
    now = now or utcnow()
    open_rows = db.execute(
        select(Alarm).where(Alarm.resolved_at.is_(None)).order_by(Alarm.opened_at.desc())
    ).scalars()
    recent = db.execute(
        select(Alarm)
        .where(Alarm.resolved_at.is_not(None))
        .order_by(Alarm.resolved_at.desc())
        .limit(RECENT_LIMIT)
    ).scalars()
    closed = (
        db.execute(
            select(Alarm).where(Alarm.resolved_at.is_not(None), Alarm.resolved_at >= now - timedelta(days=30))
        )
        .scalars()
        .all()
    )
    day = working_day_hours(settings)
    fixed = sum(1 for a in closed if working_hours_between(a.opened_at, a.resolved_at, settings) <= day)
    return {
        "open": list(open_rows),
        "recent": list(recent),
        "last_30_days": {
            "closed": len(closed),
            "fixed_within_one_working_day": fixed,
            "percent": round(100 * fixed / len(closed), 1) if closed else None,
        },
    }


# --- the watchdog --------------------------------------------------------------

#: Catches what no sweep can report about itself: a sweep that never started (3),
#: sweeps left paused (7), reminders, retries and closing.
WATCHDOG_MINUTES = 10
_watchdog: AsyncIOScheduler | None = None


async def _watchdog_tick() -> None:
    from app.db import SessionLocal

    await asyncio.to_thread(evaluate_safely, SessionLocal, get_settings())


def start_watchdog(settings: Settings) -> AsyncIOScheduler | None:
    """Start the 10-minute check on the running loop. Idempotent.

    Separate from the sweep scheduler on purpose: ``start_scheduler`` returns
    None while sweeps are paused, and pausing sweeps must not silence alarms.
    """
    global _watchdog
    if not settings.alarms_enabled or _watchdog is not None:
        return _watchdog
    watchdog = AsyncIOScheduler(timezone=settings.scheduler_timezone)
    watchdog.add_job(
        _watchdog_tick,
        IntervalTrigger(minutes=WATCHDOG_MINUTES),
        id="alarm-watchdog",
        coalesce=True,
        max_instances=1,
        # First look a minute after start-up, not ten: a container that was down
        # through a due sweep should say so promptly.
        next_run_time=datetime.now(UTC) + timedelta(minutes=1),
    )
    watchdog.start()
    _watchdog = watchdog
    log_ctx(logger, logging.INFO, "alarm watchdog started", every_minutes=WATCHDOG_MINUTES)
    return watchdog


def stop_watchdog() -> None:
    """Stop it and always drop the reference (same reasoning as stop_scheduler)."""
    global _watchdog
    if _watchdog is None:
        return
    try:
        _watchdog.shutdown(wait=False)
    except Exception as exc:
        log_ctx(logger, logging.WARNING, "alarm watchdog did not shut down cleanly", error=type(exc).__name__)
    finally:
        _watchdog = None
