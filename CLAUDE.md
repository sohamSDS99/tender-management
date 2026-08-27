# CLAUDE.md

Working notes for this repository. Everything here is a fact that cost something
to learn — most of it was a bug first. `README.md` explains the product;
`docs/DECISIONS.md` explains why it is built this way (30 records, D1–D30).

## What this is

Tender Monitor watches eight free public procurement sources, normalises every
notice, scores it for relevance to SDS/EHS software work, and surfaces the few
worth a human's time. Fetching is automated twice a day; a Slack digest announces
new high scorers. Internal network only. Notices are never edited,
but a sweep, a re-score and the schedule can all be driven from the dashboard
(D19, D21, D23), and a reviewer can mark one not relevant — after which the
system learns the pattern and hides notices like it (D27).

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
./.venv/bin/python -m pytest -q          # 622 tests, all passing
./.venv/bin/ruff check . && ./.venv/bin/ruff format --check .
./.venv/bin/alembic upgrade head         # 10 revisions, head f4a2c9e8b117

# frontend
cd frontend
npm run lint                             # tsc --noEmit
npx vitest run                           # 183 tests
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

**Reviewer feedback lives beside the frozen engine, never inside it (D27).** The
learner in `app/services/feedback.py` can *hide* a notice; it cannot move a
score, and there is no code path by which it could. `auto_irrelevant` /
`auto_irrelevant_reasons` are additive columns stamped by a separate call that
sits next to `_apply_score`, not within it. If a change would have a verdict
affect `relevance_score`, that is a much larger decision than it looks —
`test_marking_never_moves_a_relevance_score` and the baseline SHA both stand in
its way, deliberately.

## Things that will bite you

**Freeze a date only the test reads; date a field production judges against the
wall clock from the wall clock.** `tests/test_api.py` mixed the two and four
tests died of it on 2026-08-27 — `test_tender_filters[fit_statuses=manual_review]`,
`[fit_statuses=not_fit]`, `[active_only=true&minimum_score=1]` and
`test_stats_summarise_the_dashboard`. The `review-1` fixture's deadline was
`NOW + 5 days` off the frozen `test_ingest.NOW` (2026-08-21), but whether a
notice is open is decided twice by code the suite does not control: the engine
applies an expired multiplier and drops `fit_status`, and the `active_only`
clause compares against `utcnow()`. Any literal deadline therefore expires
eventually, and moving it fixes nothing —`test_date_window_filters` needs it
inside `NOW + 10 days`, so the wall clock overtakes every value in turn.

The fixture now dates **deadlines** from `DEADLINE_BASE = utcnow()` with the same
offsets, and leaves **publication dates** on `NOW` because nothing in production
compares those to now. Zero production code changed and the suite is green at
556. Do not "fix" this by re-pinning the deadlines to `NOW`; that is the bug.
The remaining alternative, if a deadline ever must be frozen, is injecting a
clock into the API — a much bigger change than it looks.

**A relationship loaded eagerly can be stale by the time you read it, and the
symptom is the opposite of the bug.** `Tender.feedback` is `lazy="selectin"`, so
any Tender already in the session has it cached — as `None` if there was no
verdict when the row was read. Inserting a `TenderFeedback` row directly left
that cache stale, and `feedback.predict` then saw no verdict and auto-hid a
notice a human had *just marked relevant*. `set_verdict` and `clear_verdict`
therefore assign through `tender.feedback` so SQLAlchemy keeps both sides in
step. Any new writer of that relationship must do the same.

**Both service caches are keyed on the data, which is right for one deployment
and wrong for a test suite.** `matching_rules.engine_for` and
`feedback.model_for` fingerprint the rows themselves, so two fresh in-memory
databases with the same counts look identical and the second test is handed the
first test's model. An autouse fixture in `conftest.py` clears both between
tests; `feedback._fingerprint` also includes `max(Tender.id)` and
`max(Tender.updated_at)` rather than only the count.

**`/api/stats` counts the *visible* population, not everything stored.** Since
D27 every count there excludes hidden notices, because the lens badges are read
straight off it and the lists those lenses open hide them. `hidden_total` carries
the remainder. So `total_tenders` is no longer the corpus size — the corpus is
`total_tenders + hidden_total`, which is exactly what the All-tenders lens badge
computes. A new count added to that response must apply `_visible_clause()` or it
will disagree with the list it labels.

**`Field(default_factory=list)` does NOT cover a column that is NULL, and the
symptom is a 500 on the list while every other endpoint looks healthy.** The
factory fills an *absent* value; an explicit `None` is a validation error. Every
JSON list column on `tenders` is nullable, and `ALTER TABLE ... ADD COLUMN`
without a server default writes `NULL` into every existing row — so
`auto_irrelevant_reasons` shipped NULL on all 807 stored notices and
`GET /api/tenders` answered 500 in production while `/api/stats` (which
serialises no tenders) and `/api/feedback/learned` were both fine. The tests
could not have caught it: every fixture row is built through the ORM, where
`default=list` supplies `[]`, so no test row was ever in the state the migration
created. `TenderListItem._null_list_is_empty` and its `TenderDetail` twin coerce
None to `[]`, and `test_a_row_from_before_the_migration_still_serialises` writes
the NULL in raw SQL, behind the ORM, which is the only way to reproduce it.
**Add a nullable JSON list column and you own this**: give it a `server_default`,
or add the field to a coercing validator, and test it with a raw-SQL NULL.

**A dashboard URL and an API URL agree on every parameter except `hidden`.**
`searchFromFilters` writes `hidden=all` for "show everything", because in the
browser's URL an *absent* `hidden` has to mean the shipped default. The API's
absent `hidden` means the opposite — ignore feedback entirely — so `buildQuery`
omits the parameter rather than translating it, and pasting a dashboard query
string at the API 422s on that one value (`bool_parsing`). The dashboard is
correct; only the hand-comparison is affected.

**`hidden` is defined twice — in SQL and as a Pydantic computed field.** There is
no avoiding it: the filter must run in the database and the boolean must reach
the browser. `test_hidden_in_sql_and_hidden_in_the_response_select_the_same_rows`
holds them together over the whole corpus. Change one, run that test.

**A Pydantic `computed_field` is sent to the browser but is not in
`model_fields`.** `test_api_contract.py` compared the frontend's declared fields
against `model_fields` alone and so reported `TenderListItem.hidden` as a field
the API never sends, while it was being sent on every row. It now reads
`model_fields | model_computed_fields`.

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

**Tests must not read the wall clock — but a frozen fixture is only half the
fix.** `tests/test_ingest.NOW` is frozen at `2026-08-21 12:00` and `test_api`
imports it, which is what keeps the date-window assertions exact. It bit twice:
first when the constant was `utcnow()` and the window tests only passed within a
day of it, then again on 2026-08-27 when `is_actionable` — computed by the
engine against the **real** clock at ingest time — quietly expired a deadline
pinned to `NOW + 5 days`. Anything whose truth depends on *now* is anchored to
`DEADLINE_BASE = utcnow()`; anything asserting an exact window stays on `NOW`.
Freezing one without the other just moves the explosion later (9efae03).

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
| `POST`/`DELETE /api/tenders/{id}/feedback` | none | one row plus a local re-predict; marks come in bursts while reading a list (D27) |

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

**There is no password for anybody but the bootstrap admin (D29).** A person
joins by opening *their own* access link and pressing one button;
`POST /api/auth/accept` creates the account and signs them in. The same link
works again for ever until revoked.

**The link IS the credential, so treat every one as a live password.** D28 said
the opposite one day earlier — "the address is the permission, not the link" —
and D29 reversed it deliberately. The whole roster response is therefore a list
of credentials, which is why it is administrators-only. Links are per person and
never shared; the workspace-wide join link D28 added was removed, not kept
alongside.

**`password_hash = ""` is the passwordless account, and `verify_password`
refuses an empty stored hash first and unconditionally.** Remove that guard and
an empty hash against an empty submitted password becomes a `"" == ""`
comparison — every link account signable-into by leaving the box blank. Two
tests pin it; neither is optional.

**Accepting is a POST, never a GET on the link itself.** Slack fetches URLs to
build previews, and a GET that established an account would let an unfurl
consume the invitation before the person saw it.

**An administrator's link enters the dashboard with no click; a member's shows
the accept screen (D30).** The page reads the link first —
`POST /api/auth/invitation`, which writes *nothing* — and branches on the role.
That is not a hole in the rule above: an unfurl fetches the page's HTML and runs
none of its JavaScript, so no preview reaches either endpoint. If you are about
to "fix" the auto-enter as an unfurl risk, read D30 first.

**The lookup reports the *account's* role when there is an account**, and the
roster entry's only when there is not. A colleague promoted under People would
otherwise be sent back to an accept screen because the entry that admitted them
still says `member`. Re-opening a link never *writes* a role —
`test_re_opening_a_link_never_re_roles_an_existing_account` pins it, and without
that guarantee "roster edits do not touch existing accounts" would hold only
until the person next opened their own link.

**Auto-entering is only for a browser with no session.** Somebody already signed
in gets nothing spent on their behalf: their own link leads where they already
are, and somebody else's must never silently swap one live session for another.
An administrator holding a colleague's link sees the accept screen with both
addresses on it **and a way out** — without that, the only button on the page
signs them out of their own account and into the colleague's, and the only escape
is a reload they have to think of.

**Two accepts of a never-used link can arrive at once, and `users.email` is
UNIQUE.** Before D30 that took two tabs and two clicks; now the page spends an
administrator's link on load, so two tabs — or a browser that *prerenders* the
URL out of the address bar and runs its JavaScript — are two accepts with no
press between them. `accept_access_link` catches `IntegrityError`, rolls back,
and re-reads the row the winner wrote. Note the shape of the argument: D29's
unfurl reasoning covers link *previews*, which execute no JavaScript, and does
**not** cover prerendering, which does. The consequence is bounded — a prerender
can only sign in the person already holding the link — but the collision was
real.

**Both public doors cap the token at `MAX_TOKEN_LENGTH`.** `/accept` and
`/invitation` are the only endpoints reachable with no session; a real token is
43 characters in a `String(64)` column, so anything longer cannot match a row and
is only a way to make an unauthenticated caller's nonsense expensive.

**The link is read in parallel with the session, not after it.** Two sequential
round trips would put the invited person behind twice as much blank frame on the
one journey the feature exists for. Effect one reads the link, effect two decides
once both answers are in — and the deciding effect has *no* cancellation on
purpose, because it sets `entering` itself and a cleanup flipping `cancelled`
would discard the result of the request it had just started.

**`role` is required on `POST /api/auth/roster`** — no default, because the role
now decides where the link lands its holder. **Re-roling somebody who has not
joined revokes their link**, so a link already sent cannot start behaving
differently from the one that was described when it was sent. Setting the role it
already has is not a change and keeps the link; a joined entry keeps its link
either way, because that link is the person's only credential.

**Only an administrator can change a role, and `tests/test_roles.py` is where
that is proved.** It covers the two endpoints that say `role` out loud *and* the
three doors that have no `require_admin` to lose — `PATCH /me`, `POST /register`
and `POST /accept` are safe because they never read a role from the caller, which
is a property of their shape and exactly the kind of thing a refactor removes by
accident. Every refusal is checked against the database, not the response body.

The remaining ways in: bootstrap when no account exists (becomes admin), and a
single-use invitation (D25) which still sets a password and is the outsider
door.

**A roster edit must not reach an existing account.** Changing an entry's role
sets what a *future* account gets; removing an address or revoking a link
withdraws the way in. None of them touches a session somebody already holds —
that is `PATCH /api/auth/users/{id}`, where the last-administrator guard lives.
Deactivation outranks a live link, or "deactivate" means nothing for exactly the
people whose only credential is one.

The first registration on an empty deployment needs no permission at all, so
between first start and first registration whoever gets there first is the
admin. Register immediately, or use `python -m app.accounts_cli create-admin`.

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

## Known-failing tests

None. This section held four for a while — `test_stats_summarise_the_dashboard`
and three `test_tender_filters` cases, dead since 2026-08-26 — and they are
fixed, in the fixture rather than in `ingest.py`. The diagnosis recorded here was
right about the cause and reached for a bigger remedy than it needed: threading a
`now` through `store_tenders`/`upsert_tender` would have touched frozen core,
when the fixture was the thing telling the lie. See the wall-clock rule above.

A green run is **594 passing, nothing skipped, nothing failing**. Treat any
failure as yours until a clean checkout says otherwise.

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
- **Anything else interactive on a card needs `position: relative; z-index: 1` and
  `stopPropagation`**, for that same overlay — the Not-relevant button included.
  Without both, the card swallows the click and opens the drawer instead.
- **`hidden` is tri-state and its default is `false`, not `null` (D27).** An absent
  `hidden` in the URL means the shipped default ("hide what was rejected"), which is
  the one place the `tribool` helper is wrong — `hidden=all` is how a link asks for
  everything. `hidden` is in `OWNED`, and it is the only thing distinguishing the
  All-tenders lens from Not relevant; drop it from `OWNED` and the first of the two
  lights for both presets.
- The default view now carries **three** chips, not two: score floor, open-only, and
  "hiding what was marked not relevant". The third is the least guessable reason a
  count is low, so it is the one that most needs saying.
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
