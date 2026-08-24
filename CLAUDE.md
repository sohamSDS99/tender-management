# CLAUDE.md

Working notes for this repository. Everything here is a fact that cost something
to learn — most of it was a bug first. `README.md` explains the product;
`docs/DECISIONS.md` explains why it is built this way (22 records, D1–D22).

## What this is

Tender Monitor watches eight free public procurement sources, normalises every
notice, scores it for relevance to SDS/EHS software work, and surfaces the few
worth a human's time. Fetching is automated twice a day; a Slack digest announces
new high scorers. Read-only for its users, internal network only, no accounts.

Runs as three containers on one machine: `docker compose up -d --build` →
dashboard on `${WEB_PORT:-8080}`, API on `${API_PORT:-8000}`, PostgreSQL internal.

## Commands

```bash
# backend — use the 3.12 venv, never the system python
cd backend
./.venv/bin/python -m pytest -q          # 321 tests
./.venv/bin/ruff check . && ./.venv/bin/ruff format --check .
./.venv/bin/alembic upgrade head         # 5 revisions, head b4efd5d106b6

# frontend
cd frontend
npm run lint                             # tsc --noEmit
npx vitest run                           # 68 tests
npm run format:check && npm run build

# a full sweep by hand (safe to repeat; every write is idempotent)
docker compose exec -T backend python -m app.jobs.scheduled_fetch --trigger cron

# deterministic demo: reload the 14 committed fixtures and post a digest
docker compose exec -T backend python -m app.jobs.scheduled_fetch --seed --seed-reset --trigger cron

# the workflow's own steps, on this machine
scripts/verify_workflow_locally.sh
```

`python3` on this host is 3.14, which has no `psycopg` wheels. The venv at
`backend/.venv` is 3.12 — always use it explicitly.

## Frozen core — read, call, wrap, never edit

- `backend/app/connectors/**` (all 8 connectors, `base.py`, `registry.py`,
  `keywords.py`, `ocds.py`)
- `backend/app/services/relevance.py`
- `config/relevance_profiles.yaml`
- the scoring/classification columns on `backend/app/models/tender.py`
- `backend/app/services/ingest.py` is **additive only** — new params defaulting to
  `None` are fine; scoring, upsert, hashing and windowing semantics are not.

`tests/test_relevance_baseline.py` pins the engine's output for the 14 seed
fixtures to a SHA-256 (`fb3ff8e6…c17d`) at a frozen instant. If it fails, the
scoring changed — do not regenerate the hash to make it pass unless that was the
deliberate intent.

## Things that will bite you

**Logs go to stderr. stdout belongs to `--json`.** The scheduled-fetch entrypoint's
JSON report is redirected into a file that CI parses; one log line on stdout makes
it unparseable. Locked by `tests/test_cli_output_contract.py`.

**Alembic's `fileConfig` will silently kill all logging.** `init_db()` runs
migrations in-process at startup, and `fileConfig`'s default
`disable_existing_loggers=True` switched off every logger the app had created and
re-pointed root at alembic's stderr handler at WARNING. Nothing after startup
logged at all — the app worked, so nothing looked wrong. `env.py` passes
`disable_existing_loggers=False` and `init_db()` re-applies `configure_logging()`.
Guarded by `tests/test_startup_logging.py`.

**Every stored datetime is naive UTC.** Dhaka is presentation and scheduling only.
Never write an aware datetime to the database.

**Never hardcode the UTC offset.** `app/jobs/schedule.py` derives the cron pair
from `ZoneInfo("Asia/Dhaka")`; `tests/test_jobs_schedule.py` asserts the workflow
YAML still agrees with the code.

**Date filters need Dhaka day boundaries.** The UI renders dates in Dhaka; sending
a bare `2026-08-31T00:00:00` had the API read it as UTC, so filtering by the date
printed on a row could exclude that row by six hours. See `dhakaDayStart`/`End` in
`frontend/src/api/client.ts`.

**`first_seen_at` is immutable, and that is load-bearing.** It is the only reason
"new in this run" is computable without touching the frozen ingest path, and it is
what the Slack digest and the New view both filter on.

**Do not recompute the relevance score in the frontend.** The engine applies caps
and multipliers and then rounds with Python's `round()` (half-to-even) where JS
`Math.round` is half-up. Inferring a cap from the difference claimed one on 64 of
283 notices. Show `relevance_score` and only explain a reduction when there is
evidence (a disqualifier, or not actionable).

**Circular import: `automation` → `scheduler` → `jobs.scheduled_fetch` →
`automation`.** `scheduler._job` imports `run_once` lazily to break it. Adding a
module-level import to any of those three can resurrect it, and it resolves
differently depending on which file a test imports first — so import each entry
point alone (`python -c "import app.main"`) after touching them.

**Tests must not read the wall clock.** `tests/test_ingest.NOW` is frozen at
`2026-08-21 12:00`; `test_api` imports that same constant. It previously called
`utcnow()`, which meant `test_date_window_filters` only passed within about a day
of that literal — it started failing on 2026-08-22.

**Exactly one trigger owner.** Either the in-process APScheduler
(`ENABLE_SCHEDULER=true`, what compose uses) or the GitHub Actions workflow
against the same database — never both, or every window is fetched twice. D2.

**The sweep schedule lives in the database, not the env var.** `SCHEDULER_HOURS_LOCAL`
is only the default; once someone sets times in the dashboard the stored value
wins and applies without a restart. D19. The same is true of *whether* it runs:
`ENABLE_SCHEDULER` is only the default for `scheduler.enabled` in `app_settings`,
and a pause set in the dashboard survives a restart. D21.

**`PUT /api/automation/trigger` must stay `async def`.** `AsyncIOScheduler.start()`
calls `asyncio.get_running_loop()`, and a sync FastAPI route runs in a threadpool
worker that has none - the switch would return 200 and never fire a sweep. Same
reason `stop_scheduler()` clears its reference in a `finally`: shutdown goes
*through* the loop the scheduler started on, so a dead loop raises, and a retained
reference would have the dashboard promise a run that cannot happen.

**`scheduler_in_process` means "the decision in force", not `ENABLE_SCHEDULER`.**
The dashboard's "switched on but not running" alarm keys on it, so if it reported
the env var a deliberate pause would read as a fault. D21.

**Slack's Web API returns HTTP 200 when it fails.** `chat.postMessage` answers
`200 {"ok": false, "error": "channel_not_found"}`, so `status_code < 400` is not
success — `_web_api_result()` reads the body. Getting this wrong is not a cosmetic
bug: the ledger writes `sent`, the unique constraint on `(tender_id,
channel_label)` means the tender is never announced again, and the notice is lost
silently. The webhook transport is different — there, the status code *is* the
answer. D22.

**`SLACK_CHANNEL_LABEL` is the ledger key, not just a label.** Changing it makes
every tender inside `SLACK_ANNOUNCE_LOOKBACK_HOURS` eligible to be announced again
under the new label. Correct when the destination channel changes, wrong when a
channel is merely renamed. D6, D22.

**Only the bot token is needed to send to Slack.** The app's signing secret,
verification token and OAuth client id/secret are for *receiving* from Slack
(slash commands, events, interactivity), which this product does not do and which
would mean exposing an endpoint against D5/D18. D22.

**`PUBLIC_APP_URL` must not be a bare IP.** The host's address moved from
`192.168.1.5` to `192.168.0.133` mid-project, which would have killed every Slack
link already sent. Use the mDNS hostname. The dashboard shows this value and warns
when it looks fragile.

## Auth boundary

Reads are open by design — the data is public procurement notices, the tool is
internal-network only, and there are no accounts (D5, D18). Writes split:

| Endpoint | Gate | Why |
|---|---|---|
| `POST /api/fetch` | `X-Cron-Secret` | spends outbound requests against 8 public services |
| `POST /api/tenders/rescore` | `X-Cron-Secret` | rewrites every stored row |
| `PUT /api/automation/schedule` | **none** | the person choosing the time in the UI *is* the authorisation (D19) |
| `PUT /api/automation/trigger` | **none** | same reasoning; pausing spends less than doing nothing (D21) |

An unset `CRON_SECRET` refuses the gated endpoints with 503 — it does not leave
them open. `tests/test_security.py` and `test_automation.py` assert the split.

## Frontend

`PRODUCT.md` owns product truth, `DESIGN.md` owns the visual world and carries an
explicit "refused" list. The previous UI was rejected outright; it is
anti-reference, not a starting point.

Dependencies are **`react` + `react-dom` only**. `vitest` is a devDependency.
Adding anything else needs a record in `docs/DECISIONS.md`.

- Desktop only, confirmed. A narrow window must degrade, not break; no phone polish.
- Views are filter *presets*, not separate state — `activeView` reads filters back
  to decide which tab is lit. `sort` is deliberately not view-owned.
- The whole filter set round-trips through the URL. The parameter names are a
  contract with the Slack digest (`minimum_score`, `active_only`, `sort`, `tender`).
- Counts are shown only where a stored total is the honest reading. Facet counts
  from unfiltered `/api/stats` beside a narrowed list promise results that are not
  there.
- Never compare filter objects with `JSON.stringify` — it compares key order too,
  and a parsed set has the same values in a different order.

## Verification habits that caught real bugs

- Run the thing, do not reason about it. The stdout/stderr bug, the false score
  cap, the dead Slack links and the empty-page trap were all found by executing.
- Browser-pane screenshots go blank once the page is scrolled. Assert against the
  DOM (`getComputedStyle`, `getBoundingClientRect`) instead.
- React state is asynchronous: dispatch an event and read the DOM in a *separate*
  tool call, or you read the pre-render value.
- Compute contrast ratios from the hex values rather than judging by eye. Two
  colours that looked fine measured 3.80:1 and 3.22:1.
- `node <impeccable>/scripts/detect.mjs --json frontend/src` after UI work.
- Review agents sometimes leave scratch test files in `src/` — check for stray
  `*probe*` files before committing, and harvest their findings first.
