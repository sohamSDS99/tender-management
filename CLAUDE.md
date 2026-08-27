# CLAUDE.md

Working notes for this repository. Everything here is a fact that cost something
to learn — most of it was a bug first. `README.md` explains the product;
`docs/DECISIONS.md` explains why it is built this way (26 records, D1–D26).

## What this is

Tender Monitor watches eight free public procurement sources, normalises every
notice, scores it for relevance to SDS/EHS software work, and surfaces the few
worth a human's time. Fetching is automated twice a day; a Slack digest announces
new high scorers. Internal network only. Notices are never edited,
but a sweep, a re-score and the schedule can all be driven from the dashboard
(D19, D21, D23).

Runs as three containers on one machine: `docker compose up -d --build` →
dashboard on `${WEB_PORT:-8080}`, API on `${API_PORT:-8000}`, PostgreSQL internal.

**On this machine the dashboard is on 8081, not 8080** — `.env` sets `WEB_PORT=8081`
because another container holds 8080. Read the port off `.env` or `docker ps` rather
than the compose default, and remember `PUBLIC_APP_URL` must match it or every Slack
link points at the wrong port.

## Commands

```bash
# backend — use the 3.12 venv, never the system python
cd backend
./.venv/bin/python -m pytest -q          # 528 tests (4 pre-existing failures, see below)
./.venv/bin/ruff check . && ./.venv/bin/ruff format --check .
./.venv/bin/alembic upgrade head         # 7 revisions, head c7e1a4b90f32

# frontend
cd frontend
npm run lint                             # tsc --noEmit
npx vitest run                           # 121 tests
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

**The two sweeps search different windows, on purpose.** A scheduled sweep looks
back 72 hours (`FETCH_MIN_LOOKBACK_HOURS`, a catch-up overlap); a sweep started
from the dashboard looks back `OPERATOR_FETCH_DAYS_BACK` (30 days). They were the
same, and that was a reported bug: the button re-queried the window the last cron
run had already emptied, so it hit eight public services, created nothing, and
reported success. Measured minutes apart on the same five connectors — 34 notices
over 72 hours, 119 over 30 days. If you ever see "Fetch finds nothing", check the
window before the connectors. D24.

**No real notice has ever scored 70, and the landing view asks for 70.**
`DEFAULT_FILTERS.minimum_score` is 70 and the Top scoring tile uses the good-fit
band, so the view a reader lands on **cannot** show a real find — every 70+ row is
a `SEED-*` fixture and the all-time real maximum is 66. This is why a sweep that
stored 8 notices looked identical to one that stored none. Anything reporting what
a sweep found must therefore filter at score 0 (`SweepReport` → the `new` view),
never inherit the default floor.

**A background sweep needs a strong reference or it can be collected.**
`asyncio.create_task`'s result was discarded in `ingest.start_fetch`, and the loop
keeps only a weak reference — a sweep sitting thirteen minutes inside one `await`
was collectable, and would vanish leaving its rows at `running` until the reaper
closed them out an hour later. `ingest._background_tasks` holds them now. Any new
`create_task` in this codebase needs the same treatment.

**The Slack digest threshold and the real data disagree.** `SLACK_MIN_SCORE` is 70
and no *real* ingested notice has ever cleared it — the highest genuine score is 66,
and every 70+ notice in the database is a `SEED-*` demo fixture. So a clean sweep
legitimately sends nothing, and the top of the dashboard is showing fixtures. Do not
read "no digest" as a broken notifier, and do not present those top cards as live
finds.

**SAM.gov's default transport is the keyless bulk extract, not the API.** `sam_use_bulk_extract`
is on, so `SAM_GOV_API_KEY` is not required and `requires_api_key` is a *property* that reports
False in that mode - which is why the credential tests pin themselves to the API path with
`sam_use_bulk_extract=False` (SAM is the only built-in with a credential, so it is the only one
that can demonstrate the mechanism). The extract is one 242 MB CSV of every *active* notice, so
it cannot query a past window or see closed notices; that is the entire reason the API path still
exists. It is streamed to a `tempfile` and parsed with the stdlib `csv` module rather than off the
socket, because descriptions are free text containing newlines inside quoted fields and splitting
the stream on newlines produces corrupt rows. `_from_extract_row()` rewrites a CSV row into the
API's field names so `_normalize()` stays one code path - do not grow a second normaliser.

**SAM.gov's quota is per day, and the free tier is 10 requests.** Not per sweep, per *day*,
shared across every run — and it is 10 only for a non-federal account with no role (1000 with
one). A 429 there carries `code 900804 "Message throttled out"` and a `Retry-After` HTTP-date
pointing at the next 00:00 UTC, so one greedy sweep locks the source out until tomorrow. The
connector used to spend up to 80 requests per sweep (`max_pages_per_source` = 20 pages, plus
`MAX_DESCRIPTION_FETCHES` = 60 per-notice description fetches), so a perfectly valid key looked
broken: production logged four SAM requests in 48 hours and every one was a 429. `sam_max_pages`
(1) and `sam_max_description_fetches` (0) now bound it to one request per sweep. Diagnose this
from the response *body*, never the status code alone — an invalid key is a different code
entirely, and "the key is wrong" is the wrong place to look.

**A key in a query string is a key in the logs.** httpx logs every request URL at INFO with the
query string intact, and SAM.gov takes its key as `?api_key=`. The redaction in
`settings/config.py` only covers the app's own log lines, so the live production key was printed
on every sweep and readable by anyone with log access. `configure_logging()` pins `httpx` to
WARNING. Any new source that authenticates by query parameter inherits this hazard.

**`base.py` clamps `Retry-After` to 120s (`MAX_RETRY_AFTER_SECONDS`).** When a server says
"not before 00:00 UTC", roughly 15 hours out, the clamp turns that into four retries in six
minutes — every one guaranteed to fail, and against SAM each one spends a request from the same
exhausted daily budget. The run is then recorded as `failed` rather than "throttled until reset".
Known, not fixed: the fix belongs in the frozen connector base and changes 429 handling for all
eight sources.

**`PUBLIC_APP_URL` must not be a bare IP.** The host's address moved from
`192.168.1.5` to `192.168.0.133` mid-project, which would have killed every Slack
link already sent. Use the mDNS hostname. The dashboard shows this value and warns
when it looks fragile.

## Auth boundary

**The dashboard is closed. Every route needs a session (D26).** This section said
the exact opposite until D26, in bold, so distrust any memory of it: D25 shipped
accounts that gated nothing, and that promise was withdrawn on request. The
reversal is the feature, not a regression.

The gate is `security.enforce_sign_in`, registered as an **application-level**
dependency in `create_app`. That placement is deliberate — a route added tomorrow
is private without anyone remembering, and exposure is the thing you have to
choose rather than privacy being the thing you have to remember. It is a
dependency rather than middleware so `app.dependency_overrides` still reaches it;
middleware would open its own database session and bypass the tests'.

**Five paths are public, and `/health` is not optional.** Railway's healthcheck
probes it with no cookie. A 401 there does not fail loudly, it fails the
*deployment* — the replica never becomes healthy, the release rolls back, and
every application log line looks fine throughout. The other four are the doors
themselves (`/api/auth/session`, `/login`, `/register`, `/logout`).

**FastAPI's docs routes do not inherit app-level dependencies.** `/docs`,
`/redoc` and `/openapi.json` are registered through Starlette's `add_route`,
while `dependencies` only reach `add_api_route` — so with `docs_url="/docs"` they
answered **200** to a stranger while every other route answered 401, handing over
every path, parameter and schema name. `create_app` registers all three itself
now. Reverting to `docs_url=...` reopens it silently;
`test_the_docs_are_behind_the_gate_too` is the tripwire.

**Signed in ≠ unlimited. These are two different axes and conflating them is the
easy mistake.** The gate decides who gets through the door. D23's cost controls
decide what they may do once inside, and they are unchanged — a signed-in
operator can hammer eight public services just as hard as an anonymous one could:

| Endpoint | Limit | Why that limit |
|---|---|---|
| `POST /api/fetch` | single-flight (409) + 300s cooldown (429) | spends outbound requests against 8 public services |
| `POST /api/tenders/rescore` | 120s cooldown (429) | rewrites every stored row |
| `PUT /api/automation/schedule` | none | choosing a time costs nothing (D19) |
| `PUT /api/automation/trigger` | none | pausing spends less than doing nothing (D21) |

`ALLOW_OPERATOR_ACTIONS=false` still closes the first two to the browser (403).

**`X-Cron-Secret` passes the gate.** A machine identity, not a hole: D5 forbids
putting it in the page so no browser holds one, and with `CRON_SECRET` unset —
the default — the door does not exist. Nothing uses it over HTTP today; the
scheduled fetch runs `python -m app.jobs.scheduled_fetch` against the database.

**`REQUIRE_SIGN_IN=false` is the way back in**, not a hedge. If the gate
misbehaves, one platform variable restores an open API without a deploy, which
beats being locked out of the tool that manages accounts. Defaults to true.

**The `client` fixture is a signed-in administrator and deliberately does NOT
send the shared secret.** The secret bypasses the gate, so a fixture carrying it
would sail past the very thing the suite should exercise — every pre-existing
test would keep passing even if the gate refused every real human. Tests that
genuinely need to skip an operator cooldown use `cron_client` and say why.

Registration is invite-only *after the first account*. The first registration on
an empty deployment needs no invite and becomes an administrator — so between
first start and first registration, whoever gets there first is the admin.
Register immediately, or use `python -m app.accounts_cli create-admin`.

**A Secure cookie over plain HTTP is never sent, and the symptom looks like a
backend bug.** Sign-in returns 200, the dashboard says nothing is wrong, and the
next request is anonymous. `SESSION_COOKIE_SECURE` is false by default because
the documented deployment is plain HTTP; set it true only behind TLS. If sign-in
"succeeds and does nothing", check this before anything else.

**There is no email transport, so there is no password reset.** Invitation links
are handed to the administrator to deliver. The recovery path for a locked-out
account is `python -m app.accounts_cli reset-password` on the host.

**`SameSite=Lax` is the entire CSRF defence, and D26 raised the stakes on it.**
It was sufficient under D25 because nothing was gated on identity, so there was
no privilege to ride. Now there is. It still holds — the dashboard is same-origin
with the API in both deployments, and Lax blocks cross-site form posts — but a
token becomes the right answer the moment anything here is exposed differently.

**A crashed sweep must not brick the Fetch button.** `_sweep_in_flight()` ignores
`fetch_runs` rows older than `STALE_RUN_MINUTES`; without that, one orphaned
`running` row disables operator fetches for ever. D23.

## Known-failing tests, not caused by you

`tests/test_api.py` has **4 failures on `main`** and has since roughly 2026-08-26:
`test_stats_summarise_the_dashboard` and three `test_tender_filters` cases.

The cause is the wall-clock hazard already described above, re-armed. The `seeded`
fixture builds deadlines from the frozen `NOW` (`2026-08-21 12:00`) but
`is_actionable` is computed by the relevance engine against the **real** clock at
ingest time, so `NOW + timedelta(days=5)` silently became a past deadline on
2026-08-26 and `actionable` fell from 3 to 2. Freezing the fixture without
freezing the server's clock only moves the explosion later.

The honest fix threads a `now` through `store_tenders`/`upsert_tender` to the
engine, which is an additive parameter but lands in `app/services/ingest.py` —
frozen-core, "additive only". Left alone deliberately; it needs a decision, not a
patch. Verify with `git stash && pytest tests/test_api.py` before blaming a branch.

## Frontend

`PRODUCT.md` owns product truth, `DESIGN.md` owns the visual world and carries an
explicit "refused" list. **DESIGN.md has been replaced twice, not edited** — first a
"quiet document" world, then the current dark instrument panel built from a supplied
mockup. Each predecessor became the anti-reference. If a new direction contradicts
DESIGN.md's refusals, replace the file rather than patching it, or it ends up
contradicting the code.

**Runtime dependencies are `react` + `react-dom` only** — nothing else ships to the
browser. The devDependencies are build and test tooling (`vite`, `typescript`,
`vitest`, `jsdom`, `prettier`, the React plugin and types). Adding a *runtime*
dependency needs a record in `docs/DECISIONS.md`. Inter is loaded from Google Fonts
with a full system fallback stack, because this host may have no route out.

- Desktop only, confirmed. A narrow window must degrade, not break; no phone polish.
- **Settings lives in the permanent left rail, pinned to its bottom**, and slides
  out from there. It is not a right drawer and not an inline column — both were
  built and both were rejected. The slide-out has no scrim so the results stay
  live beside it, and it goes `visibility: hidden` when shut, because a panel
  translated off-screen keeps its tab stops.
- **Dark is the default**, on bare `:root`; light is the `[data-theme="light"]`
  override. Preferences live under `tender-monitor:preferences:v2` — v1 is ignored
  on purpose, because its default was `system` and reading it back overrode the new
  dark default for anyone who had loaded the old page.
- Views are filter *presets*, not separate state — `activeView` reads filters back
  to decide which tab is lit. `sort` is deliberately not view-owned.
- **A tab count must equal the list the tab opens.** The score buckets therefore
  filter on score *alone* — no `active_only`, no fit filters — because the counts
  come from `/api/stats`, which counts purely on `relevance_score`. Adding a filter
  the count does not apply puts a number beside a tab that disagrees with itself.
- Tiles are filters too: clicking one narrows to exactly the population it counted.
- The result card's title is the real `<button>` and its `::after` overlays the card
  as the hit area; the "Original notice" anchor sits above it. An anchor cannot live
  inside a button, and a div-with-onClick is not keyboard reachable.
- The whole filter set round-trips through the URL. The parameter names are a
  contract with the Slack digest (`minimum_score`, `active_only`, `sort`, `tender`).
- Counts are shown only where a stored total is the honest reading. Facet counts
  from unfiltered `/api/stats` beside a narrowed list promise results that are not
  there.
- Never compare filter objects with `JSON.stringify` — it compares key order too,
  and a parsed set has the same values in a different order.

## Verification habits that caught real bugs

- Run the thing, do not reason about it. The stdout/stderr bug, the false score
  cap, the dead Slack links, the empty-page trap and the light-theme default all
  came out of executing, not reading.
- **Hard-reload before trusting any computed style.** Vite's HMR leaves the
  previous version's CSS injected, so `getComputedStyle` can return values from a
  stylesheet you already replaced. This produced a completely fictional contrast
  failure — dark tokens resolving under `data-theme="light"` — that vanished on
  reload. If a measurement contradicts the CSS you just wrote, reload first.
- **The theme attribute is written in an effect**, so two separate tool calls are
  not always enough: the first read after a theme click can still see the old
  value. Confirm `document.documentElement.dataset.theme` is what you expect
  *before* reading colours off it.
- Browser-pane screenshots go blank once the page is scrolled, and fail outright
  with "not compositing frames" unless the pane is fronted. Assert against the DOM
  (`getComputedStyle`, `getBoundingClientRect`) instead — that is the primary
  method here, not the fallback.
- React state is asynchronous: dispatch an event and read the DOM in a *separate*
  tool call, or you read the pre-render value.
- Compute contrast ratios from the hex values rather than judging by eye. Two
  colours that looked fine measured 3.80:1 and 3.22:1.
- **Do not exercise `POST /api/fetch` casually.** A full sweep is ~13 minutes
  against eight public services. To prove the endpoint path works, fetch one cheap
  source instead — `austender` returns in about a second — which runs the identical
  guard and ingest code. `/api/tenders/rescore` is free and local, so prove the
  auth path with that.
- **Verify a browser-triggered write reached the database**, not just that the UI
  said so. `select source, status, trigger from fetch_runs order by started_at desc`
  showing `trigger=manual` is the proof; a green toast is not.
- `node <impeccable>/scripts/detect.mjs --json frontend/src` after UI work.
- Review agents sometimes leave scratch test files in `src/` — check for stray
  `*probe*` files before committing, and harvest their findings first.
