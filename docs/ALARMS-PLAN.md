# Tender Monitor — alarm and email plan

Repo: `sohamSDS99/tender-management` · Owner: **Tanjir** · Built **inside** this repo, no separate system.

---

## 1. What we are building

Tender Monitor gets its own alarm. When something breaks, it emails the owner and the team.

```
                     Tender Monitor (backend container)
 ┌───────────────────────────────────────────────────────────────────┐
 │                                                                   │
 │   sweep finishes or crashes ──► alarm check runs straight away   │
 │                                                                   │
 │   every 10 minutes ───────────► alarm check runs again            │
 │                                     │                             │
 │                                     ▼                             │
 │                      "is anything broken?"                        │
 │                     /          |           \                      │
 │              new problem   still broken   was broken, now OK      │
 │                  │              │                 │               │
 │             ALARM email   after 4 working   FIXED email           │
 │                           hours: REMINDER                         │
 └───────────────────────────────────────────────────────────────────┘
          every email:  To  tanjir@sdsmanager.com
                        CC  abir@ · soham@ · shafiq@ sdsmanager.com
```

Three rules make it trustworthy:

1. **One email per problem.** Not one every 10 minutes.
2. **An alarm closes by itself** when the data shows the system works again. Nobody has to click anything. The "fixed" time is proven by the data.
3. **Alarm code can never break a sweep.** If the alarm or the email fails, the sweep carries on and the error is only logged.

---

## 2. What can break, and how the alarm knows

| # | What goes wrong | How the alarm knows | Email arrives | Closes by itself when |
|---|---|---|---|---|
| 1 | **The sweep crashed** (code error, database down mid-run) | The sweep stops with an error | Within seconds | A later sweep finishes |
| 2 | **Every source failed** | The sweep finished, but no source worked (its own exit code 1) | Within seconds | A sweep has at least one working source |
| 3 | **The sweep did not run at all** (scheduler stopped, run stuck) | A sweep was due (00:00 or 12:00 Dhaka), and nothing started within 75 minutes | About 75 min after the missed time | Any sweep starts and finishes |
| 4 | **One source keeps failing** | The same source failed in 3 sweeps in a row | After about 1.5 days | That source works once |
| 5 | **The Slack digest was not delivered** | Tenders were saved, but the Slack post failed (its own exit code 2) | Within seconds | The next digest is delivered |
| 6 | **It runs but finds nothing** | Zero notices received across all sources in 3 days | On day 3 | Any notice is received |
| 7 | **Sweeps were switched off and left off** | Sweeps paused from the dashboard for more than 1 working day | Next working day | Sweeps switched back on |

**Why "3 in a row" for one source:** single sources time out now and then. On 21 Aug `pncp` timed out while six others stored 269 notices. If every small failure sent an email, people would learn to ignore the emails.

**Why 75 minutes for a missed sweep:** the scheduler allows a sweep to start up to 60 minutes late (`misfire_grace_time=3600`), plus 15 minutes of margin.

**The hours are not hard-coded.** The sweep hours can be changed in the dashboard (1–6 runs a day). Check 3 reads them with the repo's own `get_run_hours()`, so it follows any change.

---

## 3. What it looks like in real life

**Tuesday 10 Nov, 12:00 Dhaka.** The noon sweep should start. Railway restarted the container during the night, and the server came back with a broken setting, so the scheduler is not running. Today, nobody notices for days.

**13:15.** The alarm check sees that a sweep was due at 12:00 and nothing has started. It sends:

```
To:      tanjir@sdsmanager.com
CC:      abir@sdsmanager.com, soham@sdsmanager.com, shafiq@sdsmanager.com
Subject: [ALARM] Tender Monitor — the 12:00 sweep did not run — owner: Tanjir

What broke   The 12:00 sweep did not run
Last sweep   Tue 10 Nov 00:00 (Dhaka) — finished OK
Owner        Tanjir — please fix within one working day
Fix by       Wed 11 Nov 13:15 (Dhaka) — 8 office hours (Mon–Fri 10:00–18:00)

Why
  A sweep was due at 12:00 Dhaka and none started by 13:15.
  Sweeps are switched ON in the dashboard.
  Usual causes: the scheduler did not start, or a sweep is stuck.

How to check
  Dashboard: https://<tender-url>/   (Automation panel)
  Logs:      Railway → Tender-Management → backend
```

- **Abir, Soham and Shafiq** see `owner: Tanjir` in the subject. They know it is not theirs to fix.
- **Tanjir fixes it**, then starts a sweep by hand from the dashboard.
- **When that sweep finishes**, the alarm closes and everyone gets: *"FIXED — Tender Monitor sweep is running again. Open for 2 h 40 min (within one working day)."*
- **If it is still broken after 4 office hours** (here: Tuesday 17:15), a reminder goes out. This time all four people are in **To**, not CC.

**Outside office hours:** the email still goes out straight away. But the "one working day" clock only counts office hours, **Monday to Friday, 10:00–18:00 Dhaka**:

| Alarm opens | Clock starts | Must be fixed by | Reminder (after 4 office hours) |
|---|---|---|---|
| Tue 13:15 | Tue 13:15 | Wed 13:15 | Tue 17:15 |
| Tue 02:00 (night) | Tue 10:00 | Tue 18:00 | Tue 14:00 |
| Fri 16:00 | Fri 16:00 | Mon 16:00 | Mon 12:00 |
| Sat 12:00 (weekend) | Mon 10:00 | Mon 18:00 | Mon 14:00 |

One working day = **8 office hours**. Saturday and Sunday never count.

---

## 4. What changes in the repo

Nothing in the frozen core is touched: connectors, `relevance.py`, `relevance_profiles.yaml` and scoring columns stay as they are.

### New files

| File | What it does |
|---|---|
| `backend/app/models/alarm.py` | The `alarms` table: one row per problem, with open, reminded and resolved times |
| `backend/alembic/versions/<id>_alarms.py` | Migration that creates the table |
| `backend/app/services/mailer.py` | Sends one email through the Brevo HTTP API. With no key, it only logs the email ("dry run"). |
| `backend/app/services/alarms.py` | The 7 checks, the open/remind/close logic, the working-hours clock, and the email text |
| `backend/tests/test_alarms.py` | Tests for all of the above |
| `.github/workflows/alarm-heartbeat.yml` | Every hour, from GitHub: is Tender Monitor answering at all? If not, it sends an email. See section 6. |

### Changed files (small edits)

| File | Change |
|---|---|
| `backend/app/settings/config.py` | Add the alarm settings (section 7) |
| `backend/app/models/__init__.py` | Export `Alarm` |
| `backend/app/jobs/scheduled_fetch.py` | End of `run_once`: run the alarm check. In `main()`: if the sweep crashes, open alarm 1. |
| `backend/app/services/scheduler.py` | In `_job()`: if the sweep crashes, open alarm 1 |
| `backend/app/main.py` | In `lifespan`: start the 10-minute alarm watchdog, and stop it on shutdown |
| `backend/app/api/routes.py` | `GET /api/alarms`: open and recent alarms, plus your "fixed within one working day" percentage |
| `.env.example`, `docker-compose.yml` | The new settings |
| `docs/DECISIONS.md`, `CLAUDE.md` | One new decision record, **D43 — Alarms email a named owner** (this repo writes down every decision) |

The sweep runs from three places: the container scheduler, the command line, and GitHub Actions. All three go through the same `run_once`, so one edit there covers all of them.

---

## 5. How each piece works

### 5.1 The `alarms` table

```python
class Alarm(Base):
    __tablename__ = "alarms"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    check_key: Mapped[str] = mapped_column(String(64), index=True)   # "sweep_missed"
    subject: Mapped[str] = mapped_column(String(128), default="")    # "pncp" for check 4, else ""
    title: Mapped[str] = mapped_column(String(255))
    detail: Mapped[str] = mapped_column(Text, default="")            # the "Why" in the email
    opened_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    emailed_at: Mapped[datetime | None] = mapped_column(DateTime)    # None = send again next tick
    reminded_at: Mapped[datetime | None] = mapped_column(DateTime)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, index=True)
```

**One open alarm per problem, enforced by the database:** a unique index on `(check_key, subject)` where `resolved_at IS NULL`. Both SQLite (tests) and Postgres (production) support it. In the migration, pass both `sqlite_where=` and `postgresql_where=`.

`emailed_at` stays empty if Brevo fails. The next 10-minute tick tries again, so an alarm email is never silently lost.

### 5.2 The mailer

```python
BREVO_URL = "https://api.brevo.com/v3/smtp/email"

def send(subject: str, text: str, html: str, *, to: list[str], cc: list[str],
         settings: Settings) -> bool:
    """Send one email. Never raises. Returns True only if Brevo accepted it."""
    if not settings.brevo_api_key:
        log_ctx(logger, logging.WARNING, "email not sent (dry run)", subject=subject, to=",".join(to))
        return False
    try:
        response = httpx.post(BREVO_URL, timeout=15, headers={"api-key": settings.brevo_api_key},
            json={"sender": {"email": settings.alarm_from, "name": "Tender Monitor"},
                  "to": [{"email": a} for a in to],
                  "cc": [{"email": a} for a in cc],
                  "subject": subject, "textContent": text, "htmlContent": html})
        return response.status_code < 300
    except httpx.HTTPError as exc:
        log_ctx(logger, logging.ERROR, "email failed", error=type(exc).__name__)
        return False
```

- **`httpx` is already installed**, so there is no new dependency.
- **Why Brevo and not plain SMTP:** Railway blocks outgoing SMTP on its cheaper plans, and `sds_cms` already sends through Brevo for the same reason. One Brevo account serves all four projects.
- The translator's docstring already mentions an `app/services/mailer.py` that does not exist. This file fills that name.

### 5.3 The alarm check — `alarms.evaluate(db, settings, now)`

```
problems = every check that is failing right now
           (each one = check_key, subject, title, detail)

for each problem:
    no open alarm for it  → open one, send ALARM email
    open, email failed    → try the email again
    open > 4 working hours and no reminder yet → send REMINDER (everyone in To)

for each open alarm that is NOT in problems:
    → set resolved_at, send FIXED email (with how long it was open)
```

**Each check is a small function** that reads the existing tables and returns a list of problems:

| Check | Reads |
|---|---|
| 1 crashed | an open `sweep_crashed` alarm, plus whether a sweep has finished since |
| 2 all failed | the newest `batch_id` in `fetch_runs`: are all its rows `failed`? |
| 3 missed | `get_enabled()`, `get_run_hours()`, the newest `fetch_runs.started_at` |
| 4 source streak | the last 3 `fetch_runs` rows per `source` |
| 5 Slack | the newest `batch_id` in `slack_notifications` with status `failed` or `unconfirmed` |
| 6 no data | `sum(records_received)` in `fetch_runs` over 3 days |
| 7 paused | `get_enabled()` and `enabled_changed_at()` |

### 5.4 Where it runs

| When | Where in the code | Catches |
|---|---|---|
| At the end of every sweep | end of `run_once`, via `asyncio.to_thread` (same as the notifier) | 2, 4, 5, 6 within seconds |
| When a sweep crashes | `try/except` around `run_once` in `scheduler._job()` and in `scheduled_fetch.main()` | 1 within seconds |
| Every 10 minutes | a small, separate `AsyncIOScheduler` started in `lifespan` | 3 and 7, reminders, retries, closing alarms |

**The watchdog has its own scheduler on purpose.** The sweep scheduler is not started at all when sweeps are paused (`start_scheduler` returns `None`). If the watchdog lived inside it, pausing sweeps would also silence the alarms.

Every call into alarm code is wrapped in `try/except Exception` and logs. **An alarm bug must never stop a sweep.**

### 5.5 The working-hours clock

```python
def working_hours_between(start: datetime, end: datetime, settings: Settings) -> float:
    """Hours between two UTC times that fall inside working days and hours, in Dhaka."""
```

Walk through the time in Asia/Dhaka and count only Monday–Friday, 10:00–18:00. It is simple and fast enough: an alarm is open for days, not years.

- **"Fixed within one working day"** = `working_hours_between(opened_at, resolved_at) <= hours in one working day` (8 hours: 10:00–18:00).
- **Reminder** = open for more than `ALARM_REMINDER_WORKING_HOURS` (4) office hours.

### 5.6 Your 95% number

`GET /api/alarms` (signed-in users only, like every other route) returns:

```json
{
  "open": [ ... ],
  "recent": [ ... ],
  "last_30_days": { "closed": 6, "fixed_within_one_working_day": 6, "percent": 100.0 }
}
```

Later this can become a small red banner on the dashboard. The frontend is not needed today.

---

## 6. Who watches the alarm? — the heartbeat

Everything above runs **inside** the backend container. If the whole container is down, it cannot email anyone about it. This is the one gap an alarm inside the system cannot close by itself.

**The fix is still in this repo:** a tiny GitHub Actions workflow, `alarm-heartbeat.yml`. GitHub's own servers run it, so it keeps working when Railway is down.

```
every hour:
  curl https://<tender-url>/health      ← already public, already checks the database
  if it fails AND the last heartbeat run passed  → send "[ALARM] Tender Monitor is down"
  if it passes AND the last heartbeat run failed → send "[FIXED] Tender Monitor is back"
```

- "Did the last run pass?" comes from `gh run list` on the same workflow. That way you get one email when it goes down and one when it comes back, not one every hour.
- It sends through the same Brevo API with `curl`. It needs three GitHub secrets: `BREVO_API_KEY`, `ALARM_FROM`, `TENDER_PUBLIC_URL`.
- **It never starts a sweep**, so it does not break the "one trigger owner" rule (D2).

---

## 7. New settings

```bash
# ── Alarms (D43) ──────────────────────────────────────────────
ALARMS_ENABLED=true
ALARM_OWNER_NAME=Tanjir
ALARM_TO=tanjir@sdsmanager.com
ALARM_CC=abir@sdsmanager.com,soham@sdsmanager.com,shafiq@sdsmanager.com
ALARM_FROM=alarms@sdsmanager.com          # must be a Brevo-verified sender
BREVO_API_KEY=                            # empty = dry run: emails are only logged
ALARM_WORKDAYS=mon,tue,wed,thu,fri        # office days
ALARM_WORK_HOURS=10-18                    # office hours, Dhaka time (one working day = 8 hours)
ALARM_REMINDER_WORKING_HOURS=4
ALARM_SOURCE_FAIL_STREAK=3
ALARM_NO_DATA_DAYS=3
ALARM_EXPECT_SWEEPS=auto                  # auto | always — see "Things that will bite you"
```

`ALARM_TO` takes a comma list, so a system with several owners is just more addresses.

---

## 8. Build order — one office day, 10:00 to 18:00

| Time | Step | Do this | Done when |
|---|---|---|---|
| 10:00 – 10:30 | 1 | Clone, create a branch `alarms`, set up the 3.12 venv, run the test suite | All tests pass before you change anything (883) |
| 10:30 – 11:00 | 2 | Settings + `mailer.py` + a test with a fake HTTP response | Dry run logs the email; a fake 201 returns True; a fake 500 returns False |
| 11:00 – 11:45 | 3 | `Alarm` model + migration (after head `f4a2c9e8b117`) | `alembic upgrade head` works on SQLite and on the compose Postgres |
| 11:45 – 13:00 | 4a | `alarms.py`: the working-hours clock and checks 1–4 | Unit tests pass for each |
| 13:00 – 14:00 | | **Lunch** | |
| 14:00 – 14:45 | 4b | Checks 5–7, `evaluate`, email text | Unit tests pass for each |
| 14:45 – 15:45 | 5 | Hooks: end of `run_once`, crash catch in `_job` and `main`, watchdog in `lifespan` | A forced crash opens alarm 1 |
| 15:45 – 16:15 | 6 | `GET /api/alarms` + the percentage | The test returns the right percentage |
| 16:15 – 17:00 | 7 | **Break it on purpose**, locally with `docker compose` (see below) | You have seen every email arrive, and every FIXED email |
| 17:00 – 17:30 | 8 | `alarm-heartbeat.yml` | A manual run against a wrong URL sends one email, not two |
| 17:30 – 18:00 | 9 | D43 in `DECISIONS.md`, a short `CLAUDE.md` note, `.env.example`, open the PR to Soham | PR open, CI green |

**If you run late, cut in this order:** step 8 (heartbeat), step 6 (API), check 7 (paused). They can go in a second PR tomorrow.
**Never cut:** checks 1–3 and the email with To and CC. Those are the goal.

### Tests to write — `tests/test_alarms.py`

Use the existing `settings` fixture from `conftest.py` (SQLite in memory) and replace `mailer.send` with a fake that records calls.

- A missed sweep opens **one** alarm and sends **one** email. A second tick sends nothing.
- When a sweep then finishes, the alarm closes and **one** FIXED email goes out.
- Source failed 2 times: no alarm. 3 times: alarm. Then 1 success: closes.
- If Brevo fails, `emailed_at` stays empty, and the next tick sends it.
- Reminder after 4 working hours. **Not** sent over the weekend.
- `working_hours_between`: Fri 16:00 → Mon 16:00 is exactly 8 hours; Tue 02:00 → Tue 12:00 is 2 hours; Sat → Sun is 0.
- A crash inside `run_once` opens alarm 1, and the crash still reaches the caller.
- Sweeps paused: check 3 stays quiet; check 7 fires after 1 working day.
- The alarm code raising an error does **not** change the sweep's exit code.
- Email text: To is the owner, CC has the three addresses, and the subject contains `owner: Tanjir`.

**Also:** `tests/test_relevance_baseline.py` must still pass with the same SHA. If it changes, you touched scoring by mistake.

### Breaking it on purpose (step 7)

| To trigger | Do this locally |
|---|---|
| Check 2, every source failed | Set `REQUEST_TIMEOUT_SECONDS=1`, or turn off the network, and run a sweep |
| Check 3, missed sweep | Set sweep hours to the current hour, stop the container until 75 min have passed, then start it |
| Check 1, crash | Stop the `db` container during a sweep |
| Check 5, Slack | Set `SLACK_WEBHOOK_URL` to a wrong URL |

To make it faster: temporarily set the "missed" grace period to 2 minutes. Never commit that.

---

## 9. Things that will bite you (from reading this repo)

1. **Times in the database are UTC with no timezone attached** (`utcnow()`). Compare them with `utcnow()`, never `datetime.now()`. Turn them into Dhaka time only for the email, with `format_dhaka()`.
2. **The dashboard is closed.** Every route needs sign-in (D26). That is why the email has **no "I'm on it" or "Mark fixed" buttons**. They would need new public links, which would weaken that rule. Email security scanners also "click" links automatically, which would mark alarms as handled without a person. Auto-close does the job instead.
3. **Who owns the schedule matters.** If production runs sweeps from **GitHub Actions** instead of the container, the container thinks sweeps are "off", and check 3 would stay quiet. In that case set `ALARM_EXPECT_SWEEPS=always`. **Ask Soham which one is live.**
4. **A GitHub "self-test" run** (no `DATABASE_URL` secret) writes to a throwaway database. The real database sees no sweep, so check 3 fires. That is correct: it means real sweeps are not happening.
5. **One uvicorn worker** (`backend/Dockerfile`), so the watchdog runs once. If anyone adds `--workers 2`, the unique index on open alarms still prevents duplicate alarms.
6. **Tests are SQLite, production is Postgres.** Write the checks with SQLAlchemy, not raw SQL, so both work, like the rest of the repo.
7. **The repo has strict habits.** It has 42 decision records and a frozen core. Follow them: a D43 entry, structured logging with `log_ctx`, and no new packages.
8. **Railway variables** are set in the Railway dashboard, not in the repo. Add the settings from section 7 to the `backend` service.

---

## 10. Is it ready to start?

**Yes. You can start coding now.** Nothing blocks steps 1–7: with no Brevo key, the mailer only logs the emails, so you can see them in the logs.

Needed **before going live** (not before coding):

| What | From whom |
|---|---|
| Brevo API key + a verified sender address (for example `alarms@sdsmanager.com`) | Tanjir / whoever runs Brevo for `sds_cms` |
| Is the container or GitHub Actions running the sweeps in production? | Soham |
| Rights to push a branch and open a PR on `sohamSDS99/tender-management` | Soham |
| Add the alarm settings to the Railway `backend` service, plus 3 GitHub secrets for the heartbeat | Soham / whoever has Railway access |
| The public Tender URL, for the heartbeat | Soham |

---

## 11. After Tender Monitor — the same pattern in the other three

Same idea in each repo: **checks + an alarms table + a mailer + a watchdog + one heartbeat workflow.** Same To and CC.

| Repo | What is different |
|---|---|
| Outreach Automation | Nothing is scheduled: a person starts a pipeline. So the checks are "job failed", "job stuck" and "jobs not picked up". The watchdog becomes a Celery beat task. |
| SDS Analytics | It already has a monitor (`tasks/monitoring.py`) that posts to Slack, but it is probably switched off. Add email there and switch it on. Small job. |
| Wagtail (sds_cms) | It already sends email through Brevo. Add an alarms table and a Celery beat watchdog for its 7 scheduled jobs. |
