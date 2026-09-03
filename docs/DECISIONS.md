# Architecture decisions

Decisions that are already implemented in this repository, with the reasoning that
produced them. Each record names the files that carry the decision, so a reader can
check the code rather than trust the prose. Verified against commit `7957480`.

Read D2 before enabling any scheduler, and D5 before making the API reachable from
anywhere other than the machine it runs on. **D26 supersedes both on the
question of access: the dashboard now requires a session.** D5's "reads stay
open" and D25's "accounts gate nothing" are history, not instructions.

---

## D1 — The deployment is a local Docker Compose stack, not a cloud host

**Decision.** `docker compose up -d --build` at the repository root *is* the
deployment. `docker-compose.yml` starts three services: `db` (`postgres:16-alpine`,
data in the named volume `pgdata`), `backend` (the API, published on
`${API_PORT:-8000}`), and `frontend` (nginx serving the built SPA on
`${WEB_PORT:-8080}:80`, proxying `/api` and `/health` to the API so the browser only
ever talks to one origin). `PUBLIC_APP_URL` defaults to `http://localhost:8080`.

On the machine this was built on the web port is `8081`, not `8080`: an unrelated
container (`nexus-traefik`) already publishes `0.0.0.0:8080`. That is what `WEB_PORT`
exists for; `PUBLIC_APP_URL` must be changed in step with it, or the Slack links
point at a port nothing is listening on.

**Why.** The scope was set mid-build: hosted via the local machine, no deployment
needed for now. A local stack also removes the whole class of free-tier problems a
cloud host would add (cold starts, sleeping databases, a public API surface) while
keeping the exact same containers and migrations a real host would run.

**Alternatives rejected.** A free API host plus managed Postgres (see D3 for the
database half, D4 for Railway) — more moving parts, a public API, and no benefit
while the audience is one machine. `docker compose` with SQLite on a bind mount —
rejected in D3.

**Consequences / accepted risk.** Every Slack digest entry deep-links to
`{PUBLIC_APP_URL}/?tender=<id>` (`app/services/notifier.py`, `tender_permalink`), so
with the default value the links only resolve for someone on that machine or, with a
LAN address, on the same network. Colleagues need a LAN address or a tunnel in
`PUBLIC_APP_URL`. When this does go public, `PUBLIC_APP_URL` is the one value that
must change — plus `ENABLE_API_DOCS=false` and a real `CRON_SECRET` (D5).

---

## D2 — Exactly one trigger owner, and it is the in-process scheduler

**Decision.** For the local stack the authoritative trigger is APScheduler inside the
API container: `docker-compose.yml` sets `ENABLE_SCHEDULER=true`,
`SCHEDULER_TIMEZONE=Asia/Dhaka`, `SCHEDULER_HOURS_LOCAL=0,12`, and
`app/services/scheduler.py` registers one `CronTrigger` per hour
(`hour=0` and `hour=12`, `minute=0`, in the configured zone) with
`replace_existing=True`, `coalesce=True`, `max_instances=1`,
`misfire_grace_time=3600`. `Settings.enable_scheduler` defaults to **`False`**
(`app/settings/config.py:46`), so no process ever fetches unless it was explicitly
told to.

`.github/workflows/scheduled-fetch.yml` keeps the same two schedules
(`0 18 * * *` and `0 6 * * *`, the UTC form of 00:00 and 12:00 Asia/Dhaka) and becomes
the authoritative trigger the moment a `DATABASE_URL` secret points it at a reachable
database. Both must never be enabled against the same database, or the same window is
fetched twice. Both call the same `run_once` in `app/jobs/scheduled_fetch.py`, so
there is one run implementation, not two.

**Why.** "Which process is allowed to fetch" is the kind of thing that is obvious
while building and invisible six months later. Defaulting to off makes the answer
explicit in exactly one place per environment. The local time rule lives in
`app/jobs/schedule.py` and the UTC cron strings are *derived* from it
(`utc_cron_expressions`, which computes `0 18` from `ZoneInfo("Asia/Dhaka")` rather
than hardcoding the offset); `tests/test_jobs_schedule.py` asserts the workflow file
still agrees with that function, so the two schedules cannot silently drift apart.

**Alternatives rejected.** An external cron on the host (another place to configure,
invisible to the dashboard). A distributed lock so both owners could run safely
(infrastructure for a problem that a config default solves). Hand-written UTC hours in
the workflow (unverifiable, and wrong the day the zone changes — `observes_dst()`
exists to detect that).

**Consequences / accepted risk.** A misconfiguration that enables both owners is
survivable rather than corrupting: ingest upserts on
`(source, source_notice_id)` so a re-fetch updates instead of duplicating, and the
unique constraint on `slack_notifications (tender_id, channel_label)` (D6) means the
second run cannot re-announce what the first announced. The visible symptom of a
double-fire is wasted requests and duplicate `fetch_runs` rows, not duplicate tenders
or duplicate Slack messages. `app/services/scheduler.py::scheduler_state()` reports
what is actually registered in the process, not what config asked for, so the
dashboard can say "nothing is scheduled here" rather than promise a run that will
never fire.

---

## D3 — PostgreSQL for the running app, SQLite for development and tests

**Decision.** The Compose stack runs `postgres:16-alpine` with its data in the named
volume `pgdata`, so the database survives `docker compose down` and any rebuild; the
backend container is handed
`DATABASE_URL=postgresql+psycopg://tender:...@db:5432/tenders`.
`Settings.database_url` still defaults to `sqlite:///./data/tenders.db`
(`app/settings/config.py:25`), which is what `npm run dev`-style local work and the
backend test suite use. Both engines are migrated by the same four Alembic revisions
(`8a32d37f649c` initial schema, `bd5848f10bf5` the Slack ledger, `653aa67ec5a2`
`fetch_runs.batch_id`, `935d4b1fc0ff` the `buyer_country` widening), and CI
(`.github/workflows/ci.yml`) applies them from empty on both — on PostgreSQL it also
runs `alembic downgrade base` and re-upgrades to prove they reverse.

**Why.** SQLite is the right default for tests: no server, fast, and the suite can
create and throw away a database per test. It is the wrong store for the running app,
because it does not enforce the constraints the app relies on — D9(a) is a real defect
that only PostgreSQL could have caught. Running the app on the engine that enforces
things, while keeping the fast engine for tests, gets both.

**Alternatives rejected.** SQLite everywhere including the running app — rejected by
D9(a). PostgreSQL for tests too — a server dependency and a slower suite for every
contributor, to catch a class of bug that `tests/test_schema_fit.py` now catches
engine-independently.

**Consequences / accepted risk.** Two engines means two behaviours to keep in mind
(VARCHAR enforcement being the one that already bit). Mitigated by CI running the
migrations on both and by `tests/test_schema_fit.py` comparing every seed fixture
against the declared column widths.

When this leaves the local machine, a managed free Postgres is a drop-in: change
`DATABASE_URL`, no code change. Neon is the intended primary (persistent, does not
lose data on redeploy); Supabase free is an acceptable alternative but pauses on
prolonged inactivity. Neither was provisioned, because creating the account requires
the user's own credentials — see "Not done, deliberately".

---

## D4 — Railway was rejected even though the machine is already authenticated

**Decision.** Do not deploy to Railway.

**Why.** `railway whoami` reports a logged-in account, so deploying was mechanically
possible. Two things stopped it. Railway has no free tier — the entry plan (Hobby) is
$5/month — which breaches the $0 hard constraint. And the only populated workspace
visible to that login is *Zahir Hasan's Projects* (`resplendent-truth`, `Relay-CX`,
`auto-ticket`, `Internal - Analytics`, `clovion-stage`, `clovion-app`); the user's own
workspace is empty. Deploying would have put spend on an account whose billing owner
could not be verified from here.

**Alternatives rejected.** Deploying into the personal workspace anyway and hoping the
usage stayed inside a trial credit — that is still spend on someone's card, decided
unilaterally.

**Consequences / accepted risk.** No public URL (D1). Railway remains a one-command
fallback if the user chooses to accept roughly $5/month — the containers and
migrations are already the ones a host would run, so nothing needs rewriting to take
that option.

---

## D5 — Reads stay open; every write sits behind a shared secret

**Decision.** README section 13 states the API has no authentication. That stays true
for reads: `/health`, `/api/tenders`, `/api/tenders/{id}`, `/api/sources`,
`/api/stats`, `/api/automation` and `/api/fetch-runs` are unauthenticated. Every
endpoint that writes, or that spends an outbound request, is gated:
`POST /api/fetch` and `POST /api/tenders/rescore` both declare
`dependencies=[Depends(require_cron_secret)]` (`app/api/routes.py`). The gate is
`app/security.py::require_cron_secret`: it reads the `X-Cron-Secret` header, compares
it with `secrets.compare_digest` (constant time, so latency cannot be used to guess
the value), and **fails closed** — with `CRON_SECRET` unset it raises `503` rather than
leaving the endpoint open; with a missing or wrong header, `401` plus a
`WWW-Authenticate: X-Cron-Secret` response header.

Every response also carries the hardening headers in `app/security.py::BASE_HEADERS`
(`X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`,
`Referrer-Policy: no-referrer`, `Cross-Origin-Opener-Policy: same-origin`,
`Cross-Origin-Resource-Policy: same-site`, a `Permissions-Policy` denying geolocation,
microphone and camera, and a `default-src 'none'` CSP), applied by
`SecurityHeadersMiddleware`. The `/docs`, `/redoc` and `/openapi.json` routes get a
narrower `DOCS_CSP` that permits exactly the jsdelivr assets Swagger UI needs, and
`ENABLE_API_DOCS=false` removes those routes entirely.

**Why.** The content is public procurement data that governments already publish; the
only proprietary part is the relevance scoring, and reading a score is not the risk.
The real risks are an anonymous caller triggering an eight-source sweep (outbound
requests, rate-limit exposure at the sources) or a rescore, so those are the endpoints
that need a credential. A shared secret is the right weight for a tool with no user
accounts: one value, no session handling, and CI can hold it.

**Alternatives rejected.** No gate at all (the previous state — `POST /api/fetch` was
publicly callable). Full per-user auth: no users exist to model, and it would have to
be built and maintained for a single-operator tool. Defaulting an unset `CRON_SECRET`
to "open" so the endpoints keep working out of the box — that is the failure mode this
decision exists to prevent.

**Consequences / accepted risk.** Stated plainly: there is **no rate limiting** and
**no per-user authentication**. Anyone who can reach the API can read every tender and
every fetch run, and can issue unlimited read requests. That is acceptable while D1
holds (localhost / LAN). Before the API is ever internet-reachable, set
`ENABLE_API_DOCS=false`, set a real `CRON_SECRET`, and put a rate limiter or an
authenticating proxy in front of it. `tests/test_security.py` pins the gate: anonymous
and wrong-secret calls to both write endpoints, the 503-when-unset behaviour, and the
presence of the headers on the read endpoints.

---

> **Amended by D16.** D6 describes the ledger mechanics, which are unchanged.
> The *selection* it assumed - a per-run `first_seen_at` window - was replaced
> because it made the retry described here unreachable. Read D16 with it.

## D6 — At-most-once Slack delivery, keyed on (tender, channel) — not on the run

**Decision.** `slack_notifications` holds one row per announcement, with
`UniqueConstraint("tender_id", "channel_label", name="uq_slack_notification_tender_channel")`
(`app/models/notification.py`). A tender is announced at most once per channel label,
for all time.

The sequence in `app/services/notifier.py` is claim → post → settle:

1. `qualifying_tenders` selects the tenders this run created that score at or above
   `SLACK_MIN_SCORE` and are `is_actionable` (see D7 for "created by this run").
2. `claim` writes a row per tender with `status = "pending"`
   (`CLAIMED = "pending"` in `app/models/notification.py`) and **commits before the
   HTTP POST**. A concurrent run either loses the `IntegrityError` race on the unique
   constraint or sees a fresh `pending` claim and skips those tenders.
3. `post_webhook` posts the Block Kit digest. It never raises; it returns
   `(status_code, error)`.
4. `settle` writes the outcome: `sent` with `posted_at` on success, or `failed` with
   the redacted error on failure.

Only `sent` suppresses forever. A `pending` claim blocks a sibling only while it is
fresher than `SLACK_CLAIM_STALE_MINUTES` (default `30`,
`app/settings/config.py:87`); an older one is treated as abandoned and reclaimed
(`_claimable`, and the "take it over" branch in `claim`). A `failed` row is likewise
reclaimable, so the next run retries the delivery.

**Why.** Keying the ledger on the run would have been the wrong shape. It would stop a
single run from posting the same tender twice, but it would let a *different* run —
the next scheduled sweep re-observing the same still-open notice — announce it again.
That repeat is exactly the duplicate the acceptance criteria forbid, and the one a
reader would actually notice. Committing the claim before the POST is deliberate: a
crash mid-post leaves evidence (a `pending` row) instead of a silent gap, and the
staleness window means that evidence expires instead of permanently silencing the
tender.

**Alternatives rejected.** A `notified_at` column on `tenders` (no room for a channel,
no room for a failed attempt, no provenance). Keying on
`(run_batch_id, tender_id)` — re-announces on the next run, as above. Posting first and
recording afterwards — a crash between the two loses the record and re-announces.

**Consequences / accepted risk.** A process killed between claim and settle leaves a
`pending` row: that tender is not re-announced for up to 30 minutes, then is. A Slack
outage means the digest is late, never that ingested data is lost — nothing is rolled
back, the run is reported as degraded (exit code 2, which the workflow turns into a
loud failure), and the next run retries. Changing `SLACK_CHANNEL_LABEL` changes the
key, so every previously announced tender becomes eligible to be announced once more
in the new channel; `.env.example` says so at the setting.

---

## D7 — "New in this run" is derived from `first_seen_at`, not a new column

**Decision.** `run_once` in `app/jobs/scheduled_fetch.py` captures
`started_at = utcnow()` *before* anything is written, and passes it to the notifier as
`since`. `qualifying_tenders` then filters on `Tender.first_seen_at >= since`.

**Why.** `ingest.upsert_tender` sets `first_seen_at` once, at insert, and never touches
it on a later update. So `first_seen_at >= <instant captured before the fetch>` is
exactly the set of tenders this run created — no bookkeeping column, no extra write,
and no change to the frozen ingest path (D8). It is also correct for a re-observed
notice: an amendment updates the row but not `first_seen_at`, so it is not
re-announced.

**Alternatives rejected.** A `notified` or `announced_in_batch` flag on `tenders`
(another write on the hot ingest path, and a second source of truth to keep
consistent). Diffing `records_created` counts per `FetchRun` (a count, not the
identities). Using `created_at`/`updated_at` (updated on amendment, so it would
re-announce).

**Consequences / accepted risk.** Correctness depends on `first_seen_at` staying
immutable after insert; that invariant is stated in the module docstring of
`app/services/ingest.py` and in `qualifying_tenders`. A tender inserted by some path
other than a run (for example `python -m app.seed`) is a legitimate candidate for the
next run's digest, which is what makes the seed replay in D12(d) work.

---

## D8 — The frozen core was respected; here is exactly what was touched

**Decision.** Since the baseline commit (`3d695c2`) the connectors, the relevance
engine and the scoring profile have not been edited at all: no file under
`backend/app/connectors/`, not `backend/app/services/relevance.py`, not
`config/relevance_profiles.yaml`. Two frozen-adjacent files were changed, both within
what was permitted:

* `backend/app/services/ingest.py` — additive optional `batch_id: str | None = None`
  parameters on `_create_runs`, `run_fetch` and `start_fetch`, threaded into the
  `FetchRun` rows. Scoring, upsert semantics, content hashing and window planning are
  untouched.
* `backend/app/models/tender.py` — two changes. `FetchRun.batch_id`
  (`String(64)`, indexed, nullable) is the new additive column that was explicitly
  allowed. `Tender.buyer_country` was widened from `String(8)` to `String(64)`: a
  content column, not a scoring or classification column (see D9a).

**Why.** The relevance engine's output is the product. Anything that could move a
score had to stay out of scope, and "additive, optional, defaulted to None" is the
narrowest way to add run correlation without altering behaviour for any existing
caller.

**Alternatives rejected.** Passing the batch id through a module-level global or a
context variable to avoid touching signatures — invisible coupling, worse than an
optional parameter. Recording the batch id in a side table joined on timestamps —
fragile grouping (which `automation.last_batch()` still has to fall back on for rows
written before the column existed).

**Consequences / accepted risk.** The claim is enforced, not asserted:
`tests/test_relevance_baseline.py` pins the engine's output over all 14 seed fixtures
to the SHA-256
`fb3ff8e6ba65e1f21cfa51381f9b6959e5f724f025b3c9e03a8ded734de2c17d`, and that hash is
unchanged. Any edit that moves a score, a band, a fit status or a reason string fails
that test.

---

## D9 — Two latent defects were fixed, because both blocked the acceptance criteria

**Decision.** Fix them, and keep a regression test for each.

**(a) `Tender.buyer_country` was `varchar(8)`.** The World Bank connector emits full
country names ("Indonesia"), not ISO codes. SQLite ignores VARCHAR limits, so every
test passed; PostgreSQL enforces them, so the insert raised and
`store_tenders`' per-record guard (`app/services/ingest.py`, "a malformed record only
loses itself, not the batch") caught it and dropped those notices one at a time,
counting them as `failed` and logging a warning. The result was silent partial data
loss on the only engine that matters in production, invisible to the whole suite.
Widened to `String(64)` (migration `935d4b1fc0ff`, reversible), with
`tests/test_schema_fit.py` now comparing every seed fixture against the declared
length of every capped string column — engine-independently, so the whole class of bug
fails in the fast suite. That test includes a negative control and a guard that the
limits map is actually populated, so a refactor cannot quietly disable it.

**(b) Startup migrations switched off all logging.** `backend/alembic/env.py` calls
`logging.config.fileConfig()`, whose `disable_existing_loggers` defaults to `True`, and
`app/db.py::init_db()` runs `command.upgrade(cfg, "head")` **in-process** at startup.
Every logger created before that call was therefore disabled, and the root logger was
left pointing at alembic's own stderr handler at `WARNING`. Nothing logged after
startup: not the scheduler starting, not a sweep finishing, not a Slack delivery
failing. The API kept working, so nothing looked broken — there was simply no evidence
to diagnose a missed run from, which is the one thing the runbook depends on. Fixed by
passing `disable_existing_loggers=False` in `env.py` and re-applying
`configure_logging(settings.log_level)` immediately after the upgrade in `init_db()`.
`tests/test_startup_logging.py` asserts a log line emitted after `init_db()` still
reaches a handler, that the root level and handlers survive, and that
`configure_logging` is idempotent.

**Why.** Both were in the way of the acceptance criteria rather than incidental
cleanups: (a) meant the "every source ingests on PostgreSQL" claim was false, and (b)
meant the observability the schedule depends on did not exist. Both are also the kind
of defect that stays hidden for months, which is why each got a test rather than just
a fix.

**Alternatives rejected.** (a) Truncating `buyer_country` to 8 characters at ingest —
data loss to preserve a column width chosen for the wrong assumption, and it would
have touched the frozen ingest path. (b) Removing the in-process migration so alembic's
logging config never loads — that would trade a logging bug for a "did the schema get
applied?" bug; `RUN_MIGRATIONS_ON_STARTUP` is what makes `docker compose up` a single
command.

**Consequences / accepted risk.** (a) The `935d4b1fc0ff` downgrade narrows the column
back to `varchar(8)` and will fail on rows that no longer fit — correct behaviour for a
narrowing migration, but worth knowing before running it. (b) `init_db()` now
deliberately overwrites whatever logging configuration the process had, which is why
`configure_logging` is asserted to be idempotent.

---

> **Amended after the repository was made public.** Actions minutes are now
> unlimited and free, so the monthly-allowance argument below no longer applies.
> The decision stands on its second reason, which was always the stronger one: a
> throwaway database cannot keep the notification ledger, so a scheduled run
> against one could neither honour at-most-once delivery nor keep what it
> ingested. Spending 13 minutes fetching into a database that is discarded at the
> end of the job buys nothing either way.

## D10 — A scheduled Actions run with no persistent database self-tests with fixtures

**Decision.** In `.github/workflows/scheduled-fetch.yml`, the "Decide the run mode"
step branches on whether a `DATABASE_URL` secret exists:

* **No secret + `schedule` event** → `mode=replay`: the run loads the 14 committed seed
  fixtures (`--seed --seed-reset`) and renders the Slack payload with
  `--dry-run-notify`, posting nothing. The payload is written into the step summary by
  `scripts/ci_summary.py`, and a GitHub notice explains why.
* **No secret + `workflow_dispatch`** → the dispatch input is honoured, so
  `mode: live` still performs a real eight-source sweep on demand.
* **Secret present** → every run is live and, if `SLACK_WEBHOOK_URL` is also set,
  posting.

**Why.** Two independent reasons. Cost: a live eight-source sweep was measured at 776
seconds — roughly 13 minutes, which is why the job's `timeout-minutes` is 30. Twice a
day that is about 800 Actions minutes a month against the 2,000-minute free allowance
for a private repository. Spending 40% of the allowance on a database that is deleted
when the job ends is a real risk of breaching the $0 constraint for no benefit.
Correctness: a throwaway database cannot keep the `slack_notifications` ledger, so a
run that posted from one would re-announce every qualifying tender on every run —
precisely the duplicate D6 exists to prevent.

**Alternatives rejected.** Skipping the scheduled run entirely when there is no
database — then nothing exercises the pipeline and a broken connector goes unnoticed
until the day someone adds the secret. Doing the live sweep and simply not posting —
pays the full minutes cost and still throws the data away. Committing a database file
to the repository to act as the ledger — a mutable artifact in git, and merge conflicts
on every run.

**Consequences / accepted risk.** A scheduled run on a repo with no `DATABASE_URL`
proves the pipeline, the scoring, the digest rendering and the exit-code handling, but
does *not* prove the live connectors still work — only a manual `mode: live` dispatch
or a persistent database does that. Adding one secret changes the behaviour of every
scheduled run from self-test to live-and-posting, which is intended but is also the
moment D2's "exactly one trigger owner" rule starts to matter.

---

## D11 — Accepted free-tier risks, each with its mitigation

**Decision.** Accept these three rather than engineer around them, and record the
mitigation for each.

* **GitHub disables scheduled workflows after roughly 60 days of repository
  inactivity.** Mitigation: any push to the repository re-enables them; the runbook's
  instruction is to check the Actions tab if a digest stops arriving. Not an issue at
  all while D2 holds and the in-process scheduler is the trigger owner.
* **Actions cron can be delayed under platform load**, sometimes well past the
  scheduled minute. Mitigation: the design is window-based and idempotent, never
  "assume it ran on the minute". The fetch window is always at least
  `FETCH_MIN_LOOKBACK_HOURS` (default `72`, `app/settings/config.py:34`), so
  consecutive runs overlap heavily; upserts are keyed on
  `(source, source_notice_id)`; notifications are keyed as in D6. A late run therefore
  catches up rather than missing data. The same reasoning is why the APScheduler jobs
  set `misfire_grace_time=3600` and `coalesce=True`, and why the workflow's
  `concurrency` group uses `cancel-in-progress: false` — a delayed run queues behind
  its predecessor instead of fetching the same window concurrently.
* **Actions minutes on a private repository are finite** (2,000/month free).
  Mitigation: D10, plus `timeout-minutes: 30` so a hung connector cannot burn the
  allowance.

**Why.** Each of these is a property of the free tier, not a bug to fix. What matters
is that the failure mode is "late" or "paused", never "silently wrong".

**Alternatives rejected.** A paid runner or a paid host to make the schedule reliable
(breaches $0). A keepalive workflow whose only job is to push a commit every few weeks
to defeat the 60-day rule — noise in the history, and it hides the inactivity signal
instead of surfacing it.

**Consequences / accepted risk.** A digest can be late, and on a repo relying on
Actions it can stop after ~60 days of inactivity without any error anywhere. Detection
is manual: the dashboard's automation panel shows the last batch and the next expected
run (`app/services/automation.py`), and a Slack heartbeat is posted even when a run
finds nothing (`build_heartbeat`) so silence is never ambiguous.

---

## D12 — Assumptions taken where the brief was ambiguous

**(a) "The free website" was read as all eight existing connectors.** Nothing was
narrowed: `app/connectors/registry.py::CONNECTOR_CLASSES` still lists TED, SAM.gov,
Find a Tender, Contracts Finder, World Bank, CanadaBuys, AusTender and PNCP, and the
scheduled sweep runs every source that is enabled and available. Narrowing to a subset
would have been an unrecoverable loss of coverage the user never asked for; running all
eight costs only time, and the time was measured (D10).

**(b) The mockup's "Re-score" button was removed along with the fetch buttons.**
Rescore is a write, so under D5 it now requires `X-Cron-Secret`, and a browser must
never hold that secret. `frontend/src/api/client.ts` is read-only by construction
("there is deliberately no startFetch or rescore here"), and
`frontend/src/components/SourceStrip.tsx` says the same where the fetch button used to
be. What replaced them is `app/services/automation.py` and the dashboard's automation
panel: when the next run is, how the last one went, and whether anything is broken.

**(c) Filter chips are measured against an unfiltered baseline, not the default view.**
`frontend/src/state/urlFilters.ts` compares the active filters against
`UNCONSTRAINED = { minimum_score: 0, maximum_score: 100, active_only: false }`, while
`DEFAULT_FILTERS` starts at `minimum_score: 50, active_only: true`. So the two defaults
appear as removable chips on first load. The alternative — chips only for deviations
from the default — produces a dashboard that says "9 of 320" with nothing on screen
explaining the narrowing, which is the confusing case.

**(d) A `--seed --seed-reset` replay deliberately deletes the seed rows *and* their
ledger entries.** `_seed_fixtures` in `app/jobs/scheduled_fetch.py` deletes the
`slack_notifications` rows for every `SEED-%` tender before deleting the tenders
themselves, so the re-inserted fixtures are genuinely new (fresh `first_seen_at`, no
`sent` claim) and produce a genuinely new digest on demand. This is scoped to the
`SEED-%` notice ids only; idempotency for real notices is untouched. It is what makes
the CI smoke test and the Actions replay mode meaningful, and what lets a demo be run
twice.

---

## D15 — A delivery we cannot confirm is never retried, and never hidden

**Decision.** `post_webhook` retries only when Slack *answered* with a retryable
status (429 or 5xx). A transport error — a dropped connection, a read timeout
after the request was sent — is not retried at all. The claim is settled as
`UNCONFIRMED`, which blocks re-announcement exactly like `SENT`, and the state is
surfaced through `/api/automation` (`slack.status: "unconfirmed"`), as a banner in
the dashboard, and as exit code 2 from the entrypoint. `docs/RUNBOOK.md` §4 has
the resolution procedure.

**Why.** Slack's incoming webhooks have no idempotency key, so a request that may
already have been delivered cannot be safely re-sent. This was measured, not
theorised: with a naive bounded retry, a `ReadTimeout` after delivery caused Slack
to receive the same digest **twice within one run**, and then a third time on the
next run — three deliveries of one digest, which is precisely the double-post the
acceptance criteria forbid. A status code is different: it proves Slack rejected
the message, so nothing was delivered and retrying is safe.

**Alternatives rejected.** Retry everything — measured to triple-post. Treat a
transport error as `FAILED` and retry on the next run — same duplicate, just
twelve hours later. Treat it as `SENT` silently — would lose a tender with no
signal at all, which is the one outcome that must never be invisible.

**Consequences / accepted risk.** In this narrow window a digest may genuinely
have been lost and will not resend itself; a human has to look at the channel and
either clear the row or mark it settled. That is a deliberate trade: the failure
is loud and one command from resolution, rather than a silent duplicate in a
channel of executives. Tests: `test_a_lost_response_is_delivered_at_most_once`,
`test_a_rejected_post_is_still_retried_because_nothing_was_delivered`,
`test_a_transport_error_is_never_retried_within_a_run`.

## D16 — Selection is driven by the ledger, not by a per-run time window

**Decision.** `announceable_tenders()` selects any tender that still clears the
bar, was first seen within `SLACK_ANNOUNCE_LOOKBACK_HOURS` (default 72, matching
the fetch window's floor), and has **no delivered announcement** on this channel.
Only the tenders a message will actually name are claimed; the remainder are
deferred and announced by a later run. The item cap is therefore a rate limit,
not a drop.

**Why.** The first implementation selected `first_seen_at >= <this run's start>`,
taking "newly created in this run" literally. Measured consequence: a tender
created by the 12:00 run whose digest Slack rejected was **never a candidate
again** — the 00:00 run's window began after it was created — so it was announced
by no run, ever. The retry this document promised was unreachable in production,
and the test that claimed to cover it passed only because it reused the first
run's `since` value, which the real caller never does. The same dead end applied
to a claim abandoned by a crashed process, and to every tender past the item cap,
which was marked `sent` while appearing in no message that was ever posted.

Making the ledger the only suppressor fixes all three at once, and it is what the
unique constraint was for. `first_seen_at` is immutable, so a merely re-observed
notice still cannot re-enter the candidate set: it has either been announced
(ledger) or aged out of the window.

**Alternatives rejected.** Keeping the strict per-run window and accepting the
loss — it silently drops qualifying tenders, which is the product's whole job.
Deferring the cap overflow without widening the window — the overflow would never
become eligible again, so it would not have worked.

**Consequences / accepted risk.** A tender can be announced up to 72 hours after
it was first seen if Slack was unavailable or a backlog was draining, rather than
in the run that discovered it. A run can announce something an earlier run
created, so the digest is "new since we last told you" rather than strictly "new
in this run" — a deliberate deviation from the brief's literal wording, taken
because the literal reading loses data. The backlog is bounded by the window, so
a long outage cannot produce an unbounded flood afterwards. Pinned by
`test_a_rejected_digest_is_retried_by_the_next_run`,
`test_the_item_cap_defers_the_remainder_instead_of_silently_marking_it_sent`
(which drains ten tenders past a cap of three and asserts each is named exactly
once), `test_a_tender_older_than_the_lookback_is_no_longer_announced` and
`test_a_re_observed_notice_is_never_re_announced`.

## D13 — Two dev-only frontend dependencies were added: `vitest` and `jsdom`

**Decision.** `frontend/package.json` gains `vitest` and `jsdom` as
**devDependencies**, with `npm test` wired into `.github/workflows/ci.yml`. The
shipped bundle is unchanged: 197.5 kB before and after, and the runtime
dependencies are still exactly `react` and `react-dom`.

**Why.** The brief allows a frontend dependency with a justification, and one is
warranted here. `src/state/urlFilters.ts` is load-bearing for the Slack digest:
`notifier.digest_permalink` emits `?minimum_score=<n>&active_only=true&sort=first_seen_desc`
and every entry emits `?tender=<id>`. If that codec stops understanding those
parameter names, every link in every digest silently lands on an unfiltered
dashboard — a defect that type-checks perfectly and would only be noticed live.
`src/labels.ts` carries the deadline-urgency bands and currency rendering that
deliberately mirror the backend, so it can drift silently too.

Writing the tests immediately paid for the dependency: they caught two real bugs.
`safeHref('   ')` resolved a blank feed URL against `window.location.origin` and
returned a link back into the app, and a test of mine asserted an unreachable
branch (a "1 day left" label, when anything past 72 hours is at least 3 days).

**Alternatives rejected.** No tests at all — the codec is too load-bearing.
A hand-rolled assertion script run through `node` — cheaper in dependencies, but
it needs its own DOM shims for `URLSearchParams`/`window.location` and would be a
worse-maintained test runner than the one Vite already ships against.

**Consequences / accepted risk.** `npm ci` installs more in CI. Nothing reaches
production. 32 frontend tests run in about 0.6 s.

## D14 — A run orphaned by a restart is closed out, not left "running" for ever

**Decision.** `automation.reap_interrupted_runs()` marks any `FetchRun` still at
`running` or `queued` after `STALE_RUN_MINUTES` (default 60) as `failed`, with an
explicit message saying the process stopped and that a re-run will pick up the
rest. It is called at API startup and at the start of every run.

**Why.** A run executes in-process, so it cannot survive the process dying. This
was found on the live stack: a container rebuild during a 13-minute live sweep
left one `pncp` row at `running`, and the dashboard reported the last run as
"running" indefinitely — a permanently misleading state, and exactly the kind of
thing a demo audience would ask about.

**Alternatives rejected.** Reaping every non-terminal row unconditionally at
startup — that would kill a genuinely in-flight run started by an operator via
`docker compose exec` while the API happened to restart. The age threshold is
comfortably above the ~13 minutes a full sweep takes, so a live run is never
mistaken for an orphan.

**Consequences / accepted risk.** An orphaned run stays visible as "running" for
up to an hour before it is reclassified. Ingested notices are unaffected: they
are committed per notice as they arrive, so a partial sweep keeps everything it
already stored.

## D17 — One migration is deliberately lossy in reverse, and says so

**Decision.** `935d4b1fc0ff (widen buyer country)` shortens `buyer_country`
values to eight characters in its `downgrade()` before narrowing the column.

**Why.** The revision exists because `varchar(8)` could not hold the full country
names the World Bank feed emits. Going back to that column is lossy by
definition, and on PostgreSQL the bare `ALTER` fails outright — verified: with a
single `"Indonesia"` row present, `alembic downgrade -1` raised
`StringDataRightTruncation` and left the schema mid-migration. A downgrade the
runbook promises must at least complete.

**Consequences / accepted risk.** Rolling back past this revision re-introduces
the defect it fixed: on PostgreSQL those notices are silently dropped again, one
row at a time. `docs/RUNBOOK.md` §7 therefore names restoring a dump as the
lossless path and states which single revision is the exception. Verified end to
end: `head -> base -> head` runs clean on PostgreSQL with data present.

## D18 — Internal network access, deliberately with no user accounts

**Decision.** The dashboard is reachable by anyone on the company network and has no
login, no user profiles and no per-user state. `PUBLIC_APP_URL` is set to the host's
LAN address (`http://192.168.1.5:8081`) so the deep links in a Slack digest open for
colleagues, not just on the machine running the stack. The containers already listen
on all interfaces, so no port change was needed.

Confirmed reachable across the LAN interface rather than loopback:

```
dashboard  http://192.168.1.5:8081/          -> 200
health     http://192.168.1.5:8081/health    -> 200
deep link  http://192.168.1.5:8081/?tender=1 -> 200
api        http://192.168.1.5:8081/api/stats -> 200
```

**Why.** Requested directly: internal members of the company should reach the
dashboard, with no user profiles. Adding accounts would mean a user table, a session
mechanism, a password reset path and an onboarding step for every colleague - all to
protect data that is, in substance, public procurement notices that governments have
already published. The proprietary part is the relevance scoring, and that is not
worth an auth system on an internal-only tool.

**Alternatives rejected.** Per-user accounts (cost outweighs the benefit for
internal-only reads, see above). A single shared password via nginx basic auth
(a shared password that is never rotated is theatre, and it breaks the Slack deep
links by prompting mid-navigation). Binding to loopback only (defeats the purpose -
colleagues could not open the links at all).

**Consequences / accepted risk.**

* **Reads are open to anyone on the same network.** On a trusted office LAN that is
  the intent. On an untrusted network - a cafe, a hotel, a conference - the dashboard
  becomes readable by strangers on that network for as long as the laptop is joined
  to it. Mitigation: stop the stack (`docker compose stop`) when working from an
  untrusted network, or publish only on loopback by setting the ports to
  `127.0.0.1:8081:80`.
* **Writes remain closed even from inside the network.** Verified from the LAN
  interface: `POST /api/fetch` and `POST /api/tenders/rescore` both return `401`
  without `X-Cron-Secret`. Colleagues can read everything and change nothing.
* **The database is not exposed.** `tender-monitor-db-1` publishes no host port
  (`{"5432/tcp": null}`); it is reachable only inside the compose network.
* **The LAN address can move.** It is a DHCP lease. If the router reassigns it, every
  previously sent Slack link breaks. Durable fixes, in order of preference: a DHCP
  reservation for this machine on the router; or use the mDNS hostname, which is
  verified working (`http://Sohams-MacBook-Air.local:8081/` -> 200) and survives a
  lease change, at the cost of requiring mDNS resolution on the client - fine for
  Apple and Windows 10+, unreliable on some corporate networks.
* This decision covers the **internal network only**. Nothing here changes the
  position in D5 on internet exposure: before that, turn off `ENABLE_API_DOCS` and
  put real authentication in front of it.

---

## D19 — The sweep schedule is editable from the dashboard, and the person editing it is the authorisation

**Decision.** `PUT /api/automation/schedule` takes local hours (1-6 of them, 0-23)
and is **not** behind `CRON_SECRET`, unlike every other write endpoint. The value is
stored in `app_settings` (migration `b4efd5d106b6`), so it survives a container
restart, and `scheduler.reschedule()` applies it to the running APScheduler
immediately - no restart, no redeploy. The environment variable
`SCHEDULER_HOURS_LOCAL` remains the default until someone changes it.

**Why.** Requested directly: the sweep should stay automated, but *when* it runs is
a human decision made from the UI. That framing settles the authorisation question
rather than dodging it - on an internal network tool with no accounts (D13), the
member of staff choosing the time in the dashboard *is* the authorisation. Putting a
shared secret in front of it would either mean the browser holds the secret, which
D5 forbids, or nobody can use the feature.

The environment variable could not stay the source of truth. Editing it means
recreating the container, and the value a reader sees would be whatever the image
was started with rather than what is running.

**What stays gated.** `POST /api/fetch` and `POST /api/tenders/rescore` still require
`X-Cron-Secret`, and the distinction is deliberate: those spend outbound requests
against eight public services and rewrite every stored row respectively. Choosing a
time of day does neither. Verified by test: the schedule endpoint returns 200 without
a secret while both of those return 401.

**Alternatives rejected.** Prompting for the secret in the UI (puts a shared secret
in a browser, and gives a non-technical user a password prompt for a scheduling
decision). Leaving it env-only (needs a container recreate, and the brief asked for
the opposite). Letting the timezone be edited too (every stored datetime is naive
UTC and Dhaka is a presentation concern; a web form that can move the zone invites a
class of confusion this product has no need for).

**Consequences / accepted risk.**

* **Anyone on the company network can change the sweep times.** That is the same
  trust boundary as D13 and the same accepted risk: the network is the perimeter.
  The blast radius is bounded - validation refuses anything outside 1-6 distinct
  hours in 0-23, with a message written for the person who typed it, and every
  change is logged with the old and new values.
* **A bad value cannot disable sweeps.** An empty list is refused rather than
  accepted as "never run", and a hand-edited or corrupt stored row falls back to
  the environment default instead of stopping the scheduler from starting.
  *Superseded in part by D21*: sweeps can now be stopped, but only by asking for
  exactly that, never as a side effect of a malformed schedule.
* **The GitHub Actions cron does not follow.** That schedule is static YAML in git
  and this setting cannot rewrite it. It does not matter today, because the local
  APScheduler owns the schedule (D2) and the Actions runs are an ephemeral
  self-test (D10) - but if Actions ever becomes the trigger owner, the two would
  diverge silently. `tests/test_jobs_schedule.py` asserts the YAML matches the
  *default* constant, which is what it tracks; it deliberately does not assert
  against the live database value.
* Six sweeps a day is the ceiling because one full sweep measured ~13 minutes
  against eight public services; the limit exists to stop a mis-click hammering
  them around the clock.

---

## D20 — Tender counts by source sit at the top of the page

**Decision.** A single strip under the masthead lists every source with the number
of notices stored from it, ordered by volume, each with a health pip and each
clickable to filter to that source.

**Why.** Requested directly. It also answers a question the redesign had otherwise
buried: the previous version moved source health to a collapsed section at the
bottom, which is right for *health* but wrong for *coverage* - "where is this data
coming from?" is a question a reader has on arrival, not one they go looking for.

**Consequences.** These are **stored totals** across everything ever ingested, which
is a different number from the filtered list below, so the label says "N stored,
from" rather than implying it describes the current view. A source whose fetcher
cannot run (SAM.gov without an API key) still shows its stored count, because
"1 notice, currently unavailable" is truer than hiding it. The same reasoning
removed the counts from the Fit / Hosting / Capability filter chips, where a global
total sitting beside a narrowed list promised results that were not there.

---

## D21 — Whether the sweep runs is editable from the dashboard, not just when

**Decision.** `PUT /api/automation/trigger` takes `{"enabled": true|false}`, stores it
in `app_settings` under `scheduler.enabled`, and starts or stops this process's
APScheduler immediately - no restart, no redeploy. `ENABLE_SCHEDULER` becomes the
*default* for that decision rather than the decision itself, exactly as
`SCHEDULER_HOURS_LOCAL` became the default for the hours in D19. The endpoint is
ungated, like the schedule endpoint and unlike every other write.

Carried by `app/services/schedule_settings.py` (`parse_enabled`, `get_enabled`,
`set_enabled`, `enabled_changed_at`), `app/services/scheduler.py` (`set_trigger`,
and `start_scheduler` now consulting the stored value),
`app/api/routes.py::set_trigger`, and `frontend/src/components/TriggerSwitch.tsx`.
No migration: `app_settings` is a key/value table that already exists
(`b4efd5d106b6`).

**Why.** Requested directly, and D19 had left the gap. An operator facing a source
that is rate-limiting the system, a maintenance window, or a bad deploy could change
*when* the sweep ran but not stop it, short of editing `ENABLE_SCHEDULER` and
recreating the container - which is the same objection D19 raised against leaving the
hours in the environment: the value a reader sees would be whatever the image was
started with rather than what is actually running.

**What this supersedes.** D19 listed "a bad value cannot disable sweeps" as a
property. That property was about *accidental* disabling - an empty hour list read as
"never run" - and it still holds: `parse_hours` still refuses an empty list, and
`parse_enabled` refuses anything that is not recognisably a yes or a no. What has
changed is that deliberately stopping the sweep is now possible, because a tool that
cannot be stopped from its own interface is not safer, only harder to operate.

**Why not gate it behind `CRON_SECRET`.** The same reasoning as D19: the browser
would have to hold the shared secret, which D5 forbids. And the action does not
belong with the gated ones - `POST /api/fetch` spends outbound requests against eight
public services and `/rescore` rewrites every stored row, whereas pausing spends
strictly less than doing nothing.

**Alternatives rejected.** A confirmation-free toggle (an accidental pause is
invisible for a week, which is exactly how a missed tender happens - so pausing asks
twice and resuming does not). Storing the intent without acting on the running
process, leaving it until a restart (the switch would report success and no sweep
would happen - the one failure `scheduler_state()` exists to expose). A timed pause
that auto-resumes (another scheduler to be wrong about, and it hides the state it was
meant to make safe).

**Consequences / accepted risk.**

* **A paused system looks healthy unless it says otherwise, so it says otherwise in
  three places.** The dashboard banner (`Notice.tsx`, ranked above every other warning
  because nothing else on the page will change while sweeps are off), the collapsed
  system summary (` · sweeps paused`, so a closed section cannot read as fine), and
  the control itself with the time it was paused. That visibility *is* the guard -
  prohibition was the alternative, and it is what D19 tried.
* **The route is `async def`, and must stay that way.** `AsyncIOScheduler.start()`
  binds to the running event loop via `asyncio.get_running_loop()`. A sync FastAPI
  route executes in a threadpool worker with no loop, so the switch would return 200
  and never fire a sweep. `tests/test_scheduler_jobs.py` asserts that a resume
  registers real jobs.
* **Switching sweeps on can create a second trigger owner.** If Actions also runs
  against this database, both fetch the same window (D2). D2 already established that
  this is survivable rather than corrupting - ingest upserts on
  `(source, source_notice_id)`, and the `slack_notifications` unique constraint (D6)
  stops a re-announcement - so the control says so instead of refusing. The note
  appears whenever the dashboard, rather than the environment, is what is keeping
  sweeps on.
* **Anyone on the company network can pause the sweep.** Same trust boundary and same
  accepted risk as D18 and D19: the network is the perimeter. Every change is logged,
  and pausing logs at WARNING rather than INFO precisely because the interesting case
  is the one nobody remembers doing.
* **`scheduler_in_process` in `GET /api/automation` changed meaning**, from
  "`ENABLE_SCHEDULER` is true" to "the decision in force". It had to: the dashboard's
  "switched on but not running" alarm keys on it, and a deliberate pause would
  otherwise have tripped it and read as a fault.
* **`stop_scheduler()` now clears its reference even if the shutdown fails.**
  AsyncIOScheduler shuts down *via* the loop it started on, so a loop that has already
  gone raises - and a retained reference would have the dashboard report a scheduler
  that cannot fire.
* **The GitHub Actions cron still does not follow**, for the reason D19 gives: it is
  static YAML in git and this setting cannot rewrite it.

---

## D22 — Two Slack transports, and the Web API's 200 is not success

**Decision.** The digest can be delivered either by `chat.postMessage` with a bot
token or by an incoming webhook. Which one is in force is **derived**, not
configured: `Settings.slack_transport` returns `bot_token` when
`SLACK_BOT_TOKEN` *and* `SLACK_CHANNEL_ID` are both set, `webhook` when
`SLACK_WEBHOOK_URL` is, and `none` otherwise. A bot token wins when both are
available. `post_digest()` is the single call site; `post_webhook()` is unchanged.

Carried by `app/settings/config.py` (`slack_transport`, `slack_configured`,
`SECRET_FIELDS`), `app/services/notifier.py` (`post_chat_message`,
`_web_api_result`, `post_digest`), and `app/services/automation.py` (the
dashboard reports which transport is live).

**Why derived rather than a `SLACK_TRANSPORT` env var.** A third variable makes
combinations that lie possible — `SLACK_TRANSPORT=bot_token` with no token set,
or `webhook` with no URL. Deriving it means the configuration cannot claim a
capability it does not have, and the dashboard reports the transport it resolved
to rather than the one someone typed.

**Why a bot token wins over a webhook.** It is the revocable one. An incoming
webhook URL *is* its own credential: it cannot be rotated without re-issuing it,
and anyone who has ever seen the URL can post to that channel for ever. A bot
token can be revoked and reissued without touching the channel, and its scopes
are visible and auditable in Slack.

**The defect this decision exists to prevent.** The Slack Web API answers
**HTTP 200 with `{"ok": false, "error": "..."}`** for almost every failure —
`channel_not_found`, `not_in_channel`, `missing_scope`, `invalid_auth`. The
existing webhook code treats `status_code < 400` as delivered, and reusing that
test would have been catastrophic rather than merely wrong: the ledger writes
`sent`, and the unique constraint on `(tender_id, channel_label)` (D6) means the
tender can then **never be announced again**. A silent, permanent loss of exactly
the notices the product exists to surface. `_web_api_result()` reads the body and
treats a non-JSON response as failure too, because that is what an HTML error page
from a proxy in front of Slack looks like.

**Retry policy is deliberately identical to the webhook's**, including the part
that looks over-cautious. A transport error is not retried, because
`chat.postMessage` has no idempotency key either and the request may already have
been delivered; it returns `(None, error)` so the caller records UNCONFIRMED
rather than FAILED (D15). Throttling is retried whether it arrives as a 429 or as
`ok: false, error: ratelimited`. A configuration fault is never retried — a
`channel_not_found` will not become found, and retrying only delays the report the
operator needs.

**Posting identity.** `SLACK_BOT_USERNAME` and `SLACK_BOT_ICON_EMOJI` override the
name a digest arrives under, because a Slack app is often created for something
else and its bot user is named accordingly. A tender digest arriving under the
name of whatever the app was originally built for reads as a different system
misfiring, and that is what happened here before these were set. Requires the `chat:write.customize` scope; both are
only sent when non-empty, so clearing them falls back to the app's own identity
rather than posting a blank name.

**Consequences / accepted risk.**

* **`SLACK_CHANNEL_LABEL` is both a display string and the ledger key**, so
  changing it makes already-announced tenders eligible again. That is right for a
  genuinely new destination — the new channel has not seen them — and wrong as a
  rename, where it re-announces everything still inside
  `SLACK_ANNOUNCE_LOOKBACK_HOURS`. Change the label when the channel changes, not
  when its name does.
* **Only the bot token is needed to send.** A Slack app also issues a signing
  secret, a verification token and an OAuth client id/secret; none of them are
  used here, because this product only ever *sends*. They matter for receiving
  requests from Slack — slash commands, events, interactivity — which the product
  does not do, and adding them would mean exposing an endpoint to the internet
  against D5 and D18.
* **A bot token can post where a webhook could not.** `chat:write.public` lets it
  post to any public channel without being invited, so a mistyped
  `SLACK_CHANNEL_ID` fails loudly with `channel_not_found` rather than quietly
  posting somewhere unintended — but the blast radius of a *valid* wrong ID is a
  digest in the wrong channel. The ID is checked against `conversations.list`
  during setup; see docs/RUNBOOK.md section 4b.
* **Half-configured fails closed.** A bot token with no channel resolves to
  `none`, not to `bot_token`, so it refuses to send rather than guessing a
  destination.

---

## D23 — The two expensive writes are cost-controlled, not secret-gated

**Decision.** `POST /api/fetch` and `POST /api/tenders/rescore` no longer require
`CRON_SECRET`. Anyone who can reach the API may call them, subject to server-side
limits that are *not* authentication:

* **single-flight** — a sweep is refused with **409** while one is already in
  flight, so a repeatedly clicked button produces one sweep, not eight.
* **cooldown** — **429** with `Retry-After` until
  `OPERATOR_FETCH_COOLDOWN_SECONDS` (300) has passed since the last sweep started,
  and `OPERATOR_RESCORE_COOLDOWN_SECONDS` (120) for a re-score.
* **a switch** — `ALLOW_OPERATOR_ACTIONS=false` closes both to the browser again
  and answers **403**.

`CRON_SECRET` still works and **bypasses both limits**, because CI and the
scheduled entrypoint control their own timing. `require_cron_secret()` was deleted
rather than left unused: an unused gate in a security module reads as protection
that is not there. Carried by `app/services/operator.py`,
`app/security.py::has_cron_secret`, and the two routes.

**Why.** Requested directly and non-negotiably: the redesigned dashboard has
"Fetch new tenders", "Re-score" and a per-source fetch button, and they had to
work. The alternative the brief explicitly ruled out was putting the shared secret
in the page, which D5 forbids — a secret in a browser is readable by everyone on
the network, which is strictly worse than having no secret at all.

**Why the gate was the wrong instrument anyway.** These two endpoints were never
gated for confidentiality. Reads are already completely open (D5) — the data is
public procurement notices. They were gated because one spends outbound requests
against eight public services and the other rewrites every stored row. That is
cost control, and a shared secret is a poor cost control: it says who may ask,
never how often. A single actor holding the secret could always have hammered
those eight services, and now nobody can, secret or not. In that specific sense the
limits are *stricter* than what they replaced.

**Consequences / accepted risk.**

* **Anyone on the company network can start a sweep.** That is a real widening,
  and it is the same boundary D18, D19 and D21 already accept: the network is the
  perimeter, there are no accounts, and the person acting in the dashboard is the
  authorisation. It is only defensible while the API is not reachable from the
  internet (README section 13). `ALLOW_OPERATOR_ACTIONS=false` is the switch for
  the day that changes, and it must be set before any such exposure.
* **Neither action can destroy data**, which is what makes the widening
  survivable rather than reckless. Ingest upserts on
  `(source, source_notice_id)`; a re-score recomputes a deterministic function of
  stored data. The worst outcome of a mis-click is wasted requests, and the
  cooldown bounds even that.
* **A crash mid-sweep must not brick the button.** `_sweep_in_flight()` ignores
  `fetch_runs` rows older than `STALE_RUN_MINUTES`, matching what
  `reap_interrupted_runs()` would do to them, so one orphaned row does not disable
  operator fetches for ever. Tested.
* **The unset-secret behaviour changed.** It used to answer 503 — fail closed,
  correct while the secret was the only control. With the limits in place that only
  broke the dashboard, so a deployment with no `CRON_SECRET` now works.
* **409 and 429 are deliberately different.** "Already running" resolves itself;
  "too soon" needs the caller to wait a stated number of seconds, so it carries
  `Retry-After` and a message naming the remaining time. The dashboard shows that
  message verbatim rather than a generic failure.
* **Re-score keeps its own cooldown**, derived from an `app_settings` row because a
  re-score leaves no `fetch_runs` row to read a timestamp from. A sweep and a
  re-score therefore never gate each other.

**Alternatives rejected.** Putting `CRON_SECRET` in the frontend bundle or asking
the user for it (D5; and a password prompt for "refresh the data" is absurd).
A per-IP rate limiter (D5 records no rate limiting, and IP is meaningless on a
flat office LAN). Leaving the buttons visible but disabled (the brief called this
non-negotiable, and a permanently disabled control is worse than none). A queue
with a worker (infrastructure for a problem a cooldown solves).

---

## Not done, deliberately

* **No attachment download or parsing.** Documents are linked, never fetched, so their
  text is not scored. Unchanged from the baseline; README section 13 says so.
* **No rate limiting.** Read endpoints can be called without limit (D5).
* **No per-user authentication.** There are no user accounts; writes are gated by one
  shared secret (D5).
* **No cloud deployment.** The deployment is the local Compose stack (D1); Railway was
  rejected (D4).
* **No Neon or repository-secret provisioning.** Every config file, command and
  secret name is in place (`.env.example`, `docker-compose.yml`,
  `.github/workflows/scheduled-fetch.yml`), and `CRON_SECRET` can be generated locally,
  but creating a Neon project or a GitHub Actions secret requires the user's own
  credentials. *Slack is now provisioned* — a bot token posting to a private
  channel in the company workspace; the token and channel live in `.env`, which is
  gitignored, and are not named here because this repository is public (D22).
* **No inbound Slack.** Slash commands, events and interactivity are not
  implemented, so the app's signing secret and verification token are unused. The
  product only sends (D22).

---

## D24 — An operator sweep asks a different question from the schedule, so it gets a different window

**Decision.** `POST /api/fetch` with no `days_back` now uses
`OPERATOR_FETCH_DAYS_BACK` (default **30 days**) rather than falling through to
`ingest.window()`'s floor. The dashboard sends the depth explicitly, shows it on
the button ("Fetch last 30 days") and offers 3 / 7 / 30 / 90 at the point of
action. `GET /api/automation` reports the default so the page never keeps a
second copy of the number. The frozen 72-hour floor inside `window()` is
untouched and still applies underneath, so this can never make a sweep
*shallower* than the schedule's.

Every operator sweep also now carries a `batch_id`, like a scheduled one.

**Why.** The button was reported as not working: "it is not coming up with any
new tender". It was not broken. `FetchRequest.days_back` defaulted to `None`, so
`window()` returned `max(FETCH_LOOKBACK_DAYS, FETCH_MIN_LOOKBACK_HOURS)` = 72
hours — *the same window the twice-daily cron sweep already covers*. By the time
a human clicks the button, that window contains nothing unseen, so the sweep
queried eight public services, stored almost nothing, and reported success.

Measured on 2026-08-24, same five connectors, four minutes apart:

| window | ted | find_a_tender | contracts_finder | world_bank | received |
|---|---|---|---|---|---|
| 72 hours | 5 | 18 | 0 | 11 | **34** |
| 30 days | 39 | 56 | 8 | 16 | **119** |

The two sweeps are answering different questions. The schedule's overlap is a
*catch-up* mechanism — deliberately narrow, run often, so a late amendment is
re-observed. A person pressing the button is doing the opposite: asking the
system to look harder than it does on its own. Giving both the same window made
the second one pointless.

Widening it does not risk freshness: verified that a 30-day sweep still returns
notices published the same day (find_a_tender returned 17–24 Aug), so the page
caps do not truncate the recent end.

**A second defect, in the same complaint.** Even when a sweep *did* find
something, the page could not show it. `DEFAULT_FILTERS.minimum_score` is 70 and
the Top scoring tile filters at the good-fit band — and **no real notice has ever
scored 70** (all-time maximum 66; every 70+ row in the database is a `SEED-*`
fixture). So the landing view is structurally incapable of displaying a real
find. The 12:07 sweep created 8 notices and the page went on showing the same 6
fixtures. The `New this fetch` tab carried no count, so nothing on screen ever
said otherwise. Fixed by reporting the sweep: `SweepReport` states what it found
and offers one click to exactly those notices, and the tab now carries
`last_run.records_created` — provably the same population it filters, since the
view filters on `first_seen_from = <that batch's start>`.

**Alternatives rejected.** Lowering `FETCH_MIN_LOOKBACK_HOURS` so both sweeps
look deeper — that changes frozen windowing semantics and makes every scheduled
sweep more expensive to fix a problem only the button has. Making the operator
window a persisted `app_settings` row like the schedule hours (D19) — the depth
is a per-click intent, not a standing policy, and a stored one would be wrong
the next time someone wanted a different depth. Dropping the score floor in
`DEFAULT_FILTERS` so real notices appear on landing — that changes what the
dashboard is *for*; the bar is correct, the reporting of what fell below it was
not. Leaving the window and telling the user to widen it by hand — the parameter
was only reachable by curl.

**Consequences / accepted risk.**

* **An operator sweep is more expensive than it was.** Ten times the window,
  more pages per source, so a longer sweep. The single-flight (409) and cooldown
  (429) guards from D23 therefore matter more, not less, and are unchanged and
  still tested. Nothing can be destroyed by depth: ingest upserts on
  `(source, source_notice_id)`.
* **A deep sweep is still page-capped.** `MAX_PAGES_PER_SOURCE` bounds each
  source, so "the last 90 days" means "as much of it as 20 pages reach". That
  cap is inside the frozen connectors and is not reported per run — a real gap,
  recorded rather than papered over.
* **The 90-day option can outrun a source's own retention.** Nothing breaks; the
  source simply returns what it has.
* **An operator sweep still posts no Slack digest**, because only `run_once`
  notifies. With automation paused, a high scorer found by hand is therefore
  announced by nobody until sweeps resume — the ledger-driven 72-hour window
  (D16) covers it only if a scheduled run happens inside that window. Left as
  is deliberately: wiring a browser click to Slack is a behavioural change, and
  the live `SLACK_CHANNEL_LABEL` currently disagrees with the ledger's rows,
  which would re-announce seed fixtures the moment anything posted.

**The background-task bug found on the way.** `start_fetch` discarded the result
of `asyncio.create_task`, and the event loop holds only a *weak* reference to a
task — so a sweep spending thirteen minutes inside one `await` was collectable
mid-flight, and would die leaving its rows at `running` until
`reap_interrupted_runs` closed them out an hour later. `ingest._background_tasks`
now holds a strong reference until completion. This is a bug fix in run
orchestration, not a change to the windowing, upsert, hashing or scoring
semantics that `app/services/ingest.py` freezes.

---

## D25 — The tool has user accounts now, and they deliberately gate nothing

**Decision.** There are accounts: `POST /api/auth/register`, `/login`, `/logout`,
a profile at `/me`, a session list, and an administrator's view of invitations
and people. They are carried by `app/models/user.py` (three tables: `users`,
`user_sessions`, `invites`), `app/services/accounts.py`, `app/api/auth_routes.py`
and the `Principal` dependencies at the foot of `app/security.py`. In the
dashboard: `state/auth.ts`, `components/auth/AuthDialog.tsx` and the Account
settings page.

**This reverses D18's "deliberately no user accounts" and nothing else.** Every
other position D18 took still holds, and the single most important property of
this record is what did *not* change:

> Reads are open. Writes are cost-controlled, not credential-gated (D23).
> A signed-out browser is served exactly what it was served before this record.

No tender route, no stats route, no operator action and no settings endpoint
gained a `Depends`. `tests/test_auth.py::test_reads_stay_open_after_accounts_exist`
and `::test_the_operator_actions_are_still_callable_signed_out` exist to fail
loudly if that ever stops being true. The only endpoints in the product that
refuse a caller are the ones under `/api/auth` that read or change an account —
identity here buys a profile, not access.

**Why accounts at all, then.** Because "who are you" and "what may you do" are
different questions, and the product had no answer to the first. There was no way
to have a profile, no way to tell one operator from another, and — the practical
trigger — no way to hand someone limited access to a deployment without handing
them the whole network. Answering the first question does not require starting to
gate on it, and gating would have been the larger, riskier change: the Slack
digest deep-links into the dashboard, and every one of those links would have
landed on a sign-in form.

**Registration is invite-only after the first account.** The first registration
on an empty deployment needs no invite and becomes an administrator; everyone
after that needs a single-use, expiring invite issued by an administrator. Open
self-serve signup was rejected because the perimeter is still the network (D18):
anyone who can reach the dashboard can already read everything, so open signup
would add accounts without adding a boundary, while making the *administrator*
role reachable by anyone who got there first.

**The bootstrap window is a real exposure, and it is documented rather than
closed.** Between the first start and the first registration, whoever reaches the
dashboard first becomes the administrator. It cannot be closed by a constraint —
"only one bootstrap" is not something a UNIQUE index can express — so
`docs/RUNBOOK.md` says to register immediately after the first start, and
`app/accounts_cli.py` can create an administrator from a shell on the host if
somebody beat you to it.

**Passwords use `hashlib.scrypt` from the standard library.** Not because it
beats argon2 — it does not — but because the alternative was a new runtime
dependency in a project whose entire dependency list fits on a screen, for a
password store that will hold single digits of rows. The stored format
(`scrypt$n$r$p$salt$key`) carries its own cost parameters, so raising the cost
later is a change to one constant rather than a migration. No frontend dependency
was added at all, which keeps the runtime at `react` + `react-dom` and needs no
record of its own.

**Sessions are rows, not JWTs.** A signed token cannot be withdrawn before it
expires, which would make three of the features here dishonest: "sign out
everywhere", "changing your password ends your other sessions", and
"deactivating an account ends its sessions". Revocation has to be a write
somewhere, so it is a write in `user_sessions`. The cookie is opaque, HttpOnly,
`SameSite=Lax`, and only its SHA-256 is stored — a dump of the database lets
nobody sign in as anybody.

**SameSite=Lax is the whole CSRF defence, and that is enough here.** The
dashboard is same-origin with the API in both supported deployments (Vite proxies
in development, the web container proxies in production), so Lax costs nothing
and stops another site's form from posting as you. It is acceptable as the only
control precisely because of D23: the endpoints that spend money were never
credential-gated, so there is no privilege for a forged request to ride. If any
endpoint is ever gated on identity, this record stops being sufficient and a
token becomes necessary.

**Sign-in failures are deliberately uninformative.** An unknown address, a wrong
password and a deactivated account return the same status and the same sentence,
and cost the same time — `authenticate` verifies against a dummy hash when no
user matches. The alternative turns the sign-in form into a directory of who
works here. The lockout after `LOGIN_MAX_FAILURES` is per account rather than per
address, because the API sits behind a proxy where every browser on the network
shares one apparent address.

**Two refusals protect the deployment from its own administrators.** The last
active administrator cannot be demoted or deactivated, and nobody can deactivate
themselves. Without the first, one click leaves nobody able to invite anyone;
without the second, that click is an extremely easy one to make by accident. Both
are enforced in `accounts.set_role` / `set_active`, not in the UI — the disabled
buttons only make the reason legible before the click.

**Accepted, and written down rather than solved:**

* **There is no email transport**, so an invitation link is handed to the
  administrator to deliver however they already talk to the person. Slack posts
  to a channel, not to a person, and adding a mailer for this would be a larger
  change than the feature.
* **There is therefore no self-serve password reset.** The recovery path is
  `python -m app.accounts_cli reset-password`, run by someone with a shell on the
  host — the same trust boundary that already owns the database file and `.env`.
* **`SESSION_COOKIE_SECURE` defaults to false**, because the documented
  deployment is plain HTTP on a LAN and a Secure cookie over HTTP is silently
  never sent. The failure mode is nasty and quiet — sign-in returns 200 and
  leaves you signed out — so it is named in `.env.example`, in `docker-compose.yml`
  and here. Set it true wherever the dashboard is behind TLS.
* **Nothing is attributed to a user yet.** A sweep started by a signed-in
  operator records `trigger=manual` exactly as before. Adding `triggered_by`
  would touch `fetch_runs`, and attribution was not what was asked for.

---

## D26 — The dashboard is closed. Sign-in is required, and that reverses D25

**Decision.** Every route requires a session except the five in
`app/security.py::PUBLIC_PATHS`. The dashboard renders a full-page sign-in
screen and does not mount at all until there is a session. Carried by
`security.enforce_sign_in` (registered as an *application-level* dependency in
`create_app`), `REQUIRE_SIGN_IN` in settings, `frontend/src/App.tsx` and
`components/auth/AuthPage.tsx`.

**This reverses D25 outright.** D25 shipped accounts on the explicit promise that
they gated nothing, and it was built that way on purpose. The requirement changed:
people should not see the inside of this dashboard without an account. D25 is
history now, not instruction — and a good deal of prose written under it (D5's
"reads stay open", the old README section 13, the old CLAUDE.md auth section) has
been corrected rather than left to contradict the code.

**Hiding the UI alone was never an option.** Gating the React pages while
`GET /api/tenders` still answered anybody would have been theatre: the data is
one `curl` away, and the person asking for this would reasonably believe it was
closed. So the gate is on the server, and the frontend change is a consequence of
it rather than the substance.

**The public list is five paths, and one of them is not optional.**

| Path | Why it must stay open |
|---|---|
| `/health` | **Railway's healthcheck probes it with no cookie.** A 401 here does not fail loudly, it fails the *deploy*: the replica never becomes healthy, the release rolls back, and the application logs look perfectly fine throughout. |
| `/api/auth/session` | How the page discovers it is signed out. |
| `/api/auth/login`, `/register` | The doors themselves. |
| `/api/auth/logout` | Answering 401 to "sign me out" leaves a stale cookie in the browser. |

**It is an allow-list on an app-level dependency, so new routes are private by
default.** A per-router dependency would make privacy a thing somebody has to
remember; this makes exposure the thing somebody has to choose. It is a
dependency rather than middleware specifically so `app.dependency_overrides`
still reaches it — middleware would open its own database session and quietly
bypass the one the tests inject.

**`X-Cron-Secret` still passes, and that is a door rather than a hole.** It is a
machine identity: D5 forbids putting it in the page, so no browser can hold one,
and it was already a privilege bypass before this gate existed (D23). With
`CRON_SECRET` unset — the default — `has_cron_secret` is always False and the
door is not there. Nothing currently uses it over HTTP; the scheduled fetch runs
`python -m app.jobs.scheduled_fetch` against the database directly.

**The bug this found.** With the gate wired and every route answering 401,
`/docs`, `/redoc` and `/openapi.json` still answered **200** to a stranger —
handing over every path, parameter and schema name. FastAPI registers those three
through Starlette's `add_route`, and application-level `dependencies` only reach
`add_api_route`. `create_app` now registers them itself as ordinary API routes so
they sit inside the same dependency chain. It was found by running the server and
reading the status codes, not by reading the code, and
`test_the_docs_are_behind_the_gate_too` exists so it cannot come back quietly.

**What did *not* change.** D23's cost controls are untouched and still the only
thing standing between an operator and eight public services: single-flight and a
300s cooldown on `POST /api/fetch`, 120s on `/rescore`. Being signed in gets you
through the door; it does not exempt you from what is behind it. Confusing the
two axes is the easy mistake here, and the test module docstring in
`tests/test_security.py` says so at the top.

**`REQUIRE_SIGN_IN` is a switch, defaulting to true.** Not a hedge — the way back
in. If the gate itself misbehaves, flipping one platform variable restores an
open API without a deploy, which beats being locked out of the tool that manages
the accounts. A control that defaults to off is not a control, so it defaults on,
and `test_the_gate_can_be_switched_off_to_get_back_in` covers the recovery path
because nobody discovers a broken escape hatch until they need it.

**The sign-in page is a page, not a modal — and that is a design fix, not a
preference.** `DESIGN.md` refuses "a modal for anything but the detail drawer",
so the `AuthDialog` shipped in D25 violated the design system on the day it
landed. It has been deleted. Beyond the rule, a modal implies the thing behind it
is still yours to look at, which is now exactly wrong. The replacement is an
asymmetric split — black plate, white form — because a centred card is the
reflexive shape for this screen and this product is a monochrome instrument
console, not a web form floating in space. With no colour to spend and gradients,
glass and ornament all refused, the character comes from typographic range
instead: a wordmark tracked to -0.04em against micro-labels at +0.18em. No new
font, no new colour, no new dependency.

**Consequences accepted:**

* **Slack digest deep links now land on the sign-in page.** The `?tender=` link
  survives sign-in — the page does not navigate or reload, App simply swaps the
  gate for the dashboard — so the reader arrives where the link pointed. But they
  will be asked to sign in first, every time their session has lapsed.
* **The `X-Cron-Secret` holder can read everything.** It is a credential; that is
  what credentials do. Rotate it if a non-account path is unwanted.
* **`REQUIRE_SIGN_IN=false` returns the API to D25's behaviour**, and anyone
  setting it should read this record first.

---

## D27 — A notice can be marked not relevant, and the system learns the pattern

**Decision.** A reviewer can mark any notice `relevant` or `irrelevant`. The
verdict is stored in a new `tender_feedback` table, and from the accumulated
verdicts the system derives a token log-odds model that hides *future* notices
resembling the rejected ones. Carried by `app/models/tender_feedback.py`,
`app/services/feedback.py`, two additive columns on `tenders`
(`auto_irrelevant`, `auto_irrelevant_reasons`), three endpoints
(`POST`/`DELETE /api/tenders/{id}/feedback`, `GET /api/feedback/learned`), a
tri-state `hidden` filter on `/api/tenders`, and in the dashboard the
**Not relevant** lens, the card's one-click control, the detail panel's verdict
section and the Learned-patterns table under Matching rules.

**The scoring engine is untouched, and that is the whole architecture of this
record.** `relevance.py`, `relevance_profiles.yaml` and every scoring column are
frozen, so nothing here may move a score. What feedback controls is *visibility*
only: a notice still scores what the engine says it scores, and the learner
decides whether anybody is shown it. Two consequences worth stating plainly:

> A verdict cannot make a notice score higher or lower.
> `test_feedback.py::test_marking_never_moves_a_relevance_score` fails if it does.

This split is why the feature could be added at all. Folding learned weight into
the score would have meant editing the frozen engine, would have broken
`test_relevance_baseline.py`'s pinned SHA-256, and would have made the score
unexplainable — the one property the engine was built to have.

**Verdicts are a table, not columns on `tenders`.** A notice is a record of what
a buyer published; a verdict is a record of what we thought of it. Every sweep
rewrites the first. Keeping them apart means a verdict survives a re-score, a
re-ingest and a content-hash change with nothing having to remember to preserve
it — proven by `test_a_verdict_survives_the_notice_being_amended`.

**Why log-odds against the whole corpus, and not against the notices marked
relevant.** The model is

    weight(t) = log P(t | rejected) − log P(t | everything else)

and the second half is the interesting choice. Comparing rejections against the
*rest of the corpus* rather than against explicit keeps is what makes this work
from the fifth mark instead of the five-hundredth: nobody marks things relevant
early on, so a rejected-versus-kept comparison has no denominator for weeks. It
also removes the need for a stop-word list, which is a real saving in a corpus
that is part Portuguese and part French — a word common everywhere
("contract", "services", "the") appears just as often in both halves, so its
weight lands near zero and it drops out on its own. Measured on the 611 stored
notices: `contratacao` and `especializada` earn nothing, while `pavimentacao`,
`drenagem` and `esgotamento sanitario` earn 4.5–5.8.

**No dependency, and no model artefact.** It is `collections.Counter` and
`math.log` over the stored rows, rebuilt in memory and cached against a
fingerprint of the verdict table and the corpus — the same rail
`matching_rules.engine_for` already uses, so a new verdict invalidates it
without anyone remembering to call a clear function. There is no training job,
no vector store, no scikit-learn, and nothing on disk to go stale. The runtime
dependency list is unchanged, backend and frontend both.

**Four floors, and they are the whole safety story.** Every one of them exists
to stop the feature hiding something a bidder wanted:

| Floor | Value | What it prevents |
|---|---|---|
| `MIN_MARKS` | 5 | Acting on an opinion. Under five rejections the learner predicts nothing and only explicit marks hide anything. |
| `MIN_DOC_FREQ` | 3 | One notice's buyer name or reference number becoming a rule. |
| `STRONG_AT` | 3.0 | A hide justified by a pile of weak matches with no single defensible phrase behind it. |
| the protection rule | — | Any token appearing in a notice marked *relevant* is struck out of the model entirely. A phrase present in something the team said yes to can never hide anything. |

`STRONG_AT` was added after measurement, not before. Without it, eight marks
against the 611 real notices hid 114 — and three of those were hidden purely by
Brazilian procurement boilerplate (`equipamentos`, `especializada para`) summing
past the total threshold while no single phrase said anything. The verdicts
happened to be right; the *reason on the card* was not defensible, which for
this feature is the same as being wrong. With the floor in place the same eight
marks hide 37, and because the reason displayed is always the strongest match,
the explanation is now as strong as the rule that produced it.

**Measured, on real data rather than a fixture.** Twelve marks over the 611
stored notices hide 41 (7% of the corpus), and **none** of the twelve notices
scoring 40 or above — the population a bidder actually reads — was hidden by
any of them.

**Hiding is defined twice, and a test holds the two together.** The filter runs
in SQL (`_hidden_clause` in `routes.py`) and the response is assembled in Python
(`TenderListItem.hidden`, a computed field). There is no way to have one
definition when a query must run in the database and a boolean must reach the
browser, so `test_hidden_in_sql_and_hidden_in_the_response_select_the_same_rows`
asserts they select the same rows across the whole corpus.

**`/api/stats` now excludes hidden notices from every count and reports
`hidden_total` separately.** This follows the rule that a tab's count must equal
the list the tab opens: the working lenses hide these notices, so a count that
included them would promise rows the list will not show. The difference between
`total_tenders` and the true corpus size is never merely missing — it is
`hidden_total`, which is itself the badge on the Not relevant lens and clickable
like every other number here.

**The filter is tri-state, and its default is not "off".** `hidden=false` hides
them, `hidden=true` returns only them, omitting it ignores feedback entirely. A
two-state flag would leave a mistaken mark unreachable, and an undo that cannot
find what it is undoing is not an undo. In the dashboard's URL codec an absent
`hidden` means the shipped default rather than "do not care" — the one place the
existing `tribool` helper would have been wrong, because a plain link to the
dashboard must not show back everything somebody rejected.

**Marking is gated but uncapped, and those are two different axes.** All three
endpoints are private, and none of them had to ask: `enforce_sign_in` is an
application-level dependency (D26), so a route added afterwards is closed by
default. `test_marking_is_behind_the_sign_in_gate_like_everything_else` asserts
that actually held for these three rather than trusting that it did.

Past the door there is no limit at all. D23's rule is that a write is
constrained by a limit matching its cost. A sweep spends outbound requests
against eight public services and a re-score rewrites every row, so both carry
cooldowns. A verdict spends neither: it writes one row and re-runs a local pass
over the corpus. It is also made in bursts of a dozen while somebody works
through a page, so a cooldown would be rate-limiting the act of reading a list.

**The Slack digest respects it.** `qualifying_tenders` and
`announceable_tenders` both exclude hidden notices. A digest is the one place
this tool interrupts a person, so drawing from a different population than the
dashboard shows would mean a notice rejected on Monday being pushed into a
channel on Tuesday — at which point the mark reads as broken. Marks made after a
digest has gone out are honoured for every later run; nothing is retroactively
unsent, because the ledger's unique constraint means an announcement never
repeats anyway.

**Every pattern is readable, and that is the price of being allowed to hide
anything.** The phrase lists in `relevance_profiles.yaml` can be argued with by
reading them. A learned pattern cannot, unless it is shown with how many
rejections it appears in and how many other notices it does not — so
`GET /api/feedback/learned` returns exactly that and the Learned-patterns table
renders it. A pattern that looks wrong is a mark that was wrong, and the
Not relevant lens is where that mark is withdrawn.

**Accepted, and written down rather than solved:**

* **Re-predicting is a linear pass over every stored notice on every mark.**
  Milliseconds at a few thousand notices, which is the size this tool is built
  for; the ceiling is named in a `ponytail:` comment in `apply_to_corpus`. If the
  corpus reaches six figures it belongs in the sweep, re-predicting only what
  changed.
* **The four floors are constants, not settings.** They were calibrated against
  this corpus and exposing them would invite tuning a statistical threshold by
  feel. The Learned-patterns screen shows the evidence instead, which is the
  control that actually helps.
* **No attribution, and D26 makes that a choice rather than a limitation.**
  Since sign-in is required there is now always a principal to record, so
  `decided_by` became possible exactly when this landed - and is still absent.
  It was not asked for, and it would put a named person on a judgement about
  public data for the first time. The column is a migration away if the team
  ever wants "who hid this?"; what it is not is an accident.
* **Descriptions are learned from, but the list endpoint does not return them**,
  so a card's reason can name a phrase the reader cannot see on that card. The
  detail panel shows the description, which is where the reason is checkable.

---

## D28 — The address is the permission, not the link

**Decision.** A workspace **roster** lists the email addresses allowed to hold an
account, and one durable **join link** is shared with everybody on it.
Registration requires both: a current link *and* an address on the roster.
Carried by `app/models/roster.py`, `app/services/roster.py`, the `/api/auth/roster`
endpoints and `components/settings/WorkspaceRoster.tsx`.

**Why, when D25 already had invitations.** Because invitations made every new
colleague a clerical task. One token per person, shown once, copied before the
box closed, pasted somewhere, repeated — and if the box closed first, the token
was unrecoverable and the whole dance started again. For a team joining an
internal tool that is the wrong shape of work.

The roster inverts which half is secret. An administrator writes down who
belongs, sends the team one link, and each person lets themselves in.

**That inversion is the entire security argument, so it is worth stating
plainly.** Because the roster decides, the link is *not* a bearer token:

* it is stored **readably** in `app_settings` and can be shown again next month,
  where an invite token is hashed and shown once
* it is **reusable**, where an invite is single-use
* a leaked one is only useful to somebody already on the roster, and they were
  welcome anyway

That is what makes it safe to paste into a team channel, which is exactly what
it is for. `tests/test_roster.py` leads with the three refusals that this rests
on — right link with an unlisted address, an address removed after the fact, and
a rotated link killing a stale copy. If the first of those ever passes, the link
has silently become a bearer token and the roster is decoration.

**Both mechanisms are kept**, because they answer different questions. The
roster answers "let my team in". A single-use invitation still answers "let this
one person in" — a contractor, an outsider, somebody with no company address.
Removing invites would have made the second case impossible.

**Two things a roster edit deliberately does not do**, both because conflating
them causes harm:

* **Changing an entry's role does not move an existing account.** The roster
  records the role somebody gets *on joining*. Re-roling a colleague who joined
  last week as a side effect of tidying a list is a change nobody asked for,
  happening in the wrong place. The control for that is
  `PATCH /api/auth/users/{id}`, and the UI disables the roster's role toggle
  once an entry has joined rather than leaving a control that silently does
  nothing.
* **Removing an address does not close the account it became.** Withdrawing
  permission to *register* is not the same act as ending somebody's access,
  which kills their sessions and is guarded against stranding the last
  administrator. If removal also closed accounts, a roster tidy-up could lock
  everybody out of the deployment.

**Pasting is the input, not a form.** `parse_addresses` accepts commas,
semicolons, spaces and newlines together, because that is what comes out of a
mail client's To: field, a spreadsheet column and a Slack message respectively.
Asking somebody to reformat a list they already have is the friction this
feature exists to remove. Duplicates are dropped silently; **bad addresses are
named**, because a typo that vanishes without comment becomes a colleague who
cannot get in and nobody knowing why. Re-pasting a team list to add one person
reports the rest as already present rather than erroring, and leaves their roles
alone.

**Named `roster`, not `members`.** "Member" is already a *role* here, as against
an administrator, so a `workspace_members` table holding rows whose role is
`admin` would be a sentence arguing with itself.

**One workspace, not many.** This deployment is the workspace: one set of
tenders, one relevance config, one team. Multi-tenancy would mean a workspace
column on every table and scoping on every query, where one missed `WHERE` leaks
one organisation's data to another — a large, risky change with nothing here
asking for it. The table is named so a second workspace could be added later
without renaming anything, and no scoping logic exists until something actually
needs it.

**Accepted:**

* **Adding somebody to the roster notifies nobody.** It records who is allowed
  in; delivering the link stays a separate, deliberate act. D25 declined an email
  transport for exactly this and nothing since has added one — the link goes out
  over Slack by hand.
* **The join link is readable by any administrator.** That is the point, and it
  is not an escalation: an administrator can already add addresses and issue
  invitations.
* **A roster entry is not an account and outlives one.** Deleting a user leaves
  the entry, so the address can register again. That is usually what is wanted
  when somebody's account is recreated, and it is why `joined_user_id` is a
  nullable link rather than a boolean.

---

## D29 — There is no password. The link is the credential

**Decision.** Every roster entry carries its own durable access link. Opening it
and pressing **Accept** creates the account if it does not exist and signs the
person in. No password is set, then or ever. The same link works again next
month on a different device until an administrator revokes it. Carried by
`roster.access_token`, `accounts.accept_access_link`, `POST /api/auth/accept`
and the accept screen in `components/auth/AuthPage.tsx`.

**This reverses D28, one day later, and the reversal is the point.** D28's rule
was *"the address is the permission, not the link"* — which is exactly what made
a single shared link safe to post in a team channel. The requirement changed to
"the link getters only have to just accept the invitation, nothing else is
needed", and once clicking is sufficient, the link **is** the credential. Both
records are kept because the earlier reasoning is still correct about the design
it describes; it simply is not the design any more.

**What that costs, stated plainly.** Whoever holds somebody's link is that
person. A link forwarded, screenshotted, or left in Slack history is a way in
until it is revoked. That was accepted knowingly for an internal tool, against
the alternative: no password *and* no email transport (D27, rejected) means a
one-shot link leaves somebody with no way back the moment their session lapses.
A permanent link is the only shape where "nothing else is needed" stays true
past the first fortnight.

**Therefore links are per person, never shared.** A shared one under these rules
would let anybody who saw it become somebody. The workspace-wide join link from
D28 is removed rather than left alongside — two doors where one is wanted is
worse than either, and that one also let a stranger self-register *with* a
password, which is the thing this record deletes.

**The empty password hash is the one place this could open a hole.** An account
created from a link stores `password_hash = ""`. `verify_password` refuses an
empty stored hash **first and unconditionally**, before parsing anything —
without that, an empty hash against an empty submitted password is the shape of
a `"" == ""` comparison, and every passwordless account would be signable-into
by anybody who left the box blank. Asserted twice: directly against
`verify_password`, and through `POST /api/auth/login` with blank, whitespace and
ordinary passwords.

**Accepting is a POST, not a GET on the link.** Slack and every other chat
client fetches a URL to build a preview. If opening the link were a GET that
established an account, an unfurl would consume the invitation before the person
ever saw it. The button also gives them a moment to see what they are joining,
which a silent redirect does not.

**Revoking is setting the token to null**, which is why there is no separate
revoked flag to disagree with it. Revoking does **not** end a session the person
already holds: the link grants sign-in, and a live session stands on its own.
Cutting somebody off entirely is revoke *plus* deactivating the account — and
deactivation outranks a live link, or "deactivate" would mean nothing for
precisely the people whose only credential is one.

**Tokens are stored readably**, like D28's join link and unlike an invite token.
An administrator has to be able to re-send one, and "I lost the link" must not
mean "you are locked out until I mint another". The security delta is smaller
than it looks: an administrator can already mint a link for any address, so
reading the column grants nothing they lack, and against a database leak this is
a bearer token for one application with no reuse value elsewhere — unlike a
password hash, whose value is that people reuse passwords.

**Accepted:**

* **Single-use invitations (D25) are unchanged and still set a password.** They
  are the outsider door — somebody with no company address — and were left alone
  rather than converted, so that path is inconsistent with this one on purpose.
* **`sent`-style delivery is still manual.** Adding somebody mints their link;
  an administrator sends it over Slack. D27 built email for this and it was
  rejected as unnecessary.
* **A link in Slack history outlives the conversation.** Revocation is the only
  answer, and it is one click per person in the roster panel.

---

## D30 — An administrator's link lands in the dashboard; a member's asks first

**Decision.** Opening an access link no longer does one thing. The page reads the
link before spending it, and then either enters or asks:

* **an administrator** goes straight into the dashboard, with nothing to press
* **a member** sees the accept screen — their address, the role they are joining
  as, and one button

Carried by `POST /api/auth/invitation` (read-only) and
`accounts.describe_access_link`, the `landsStraightInDashboard` rule in
`state/auth.ts`, the `checking` / `entering` / `ready` / `dead` branches in
`App.tsx`, and the two screens in `components/auth/AuthPage.tsx`.

**Why the two differ at all.** An administrator is the person who *hands out*
links and sets the workspace up; asking them to confirm an invitation of a shape
they authored is a step with nothing behind it. A member is joining something for
the first time, and the accept screen is where they are told what — by whose
address, as what role — before anything is created in their name. The asymmetry is
the feature, not an optimisation of it.

**This does not reverse "accepting is a POST, never a GET" (D29).** That rule
exists because a chat client fetches a URL to build a preview, and a GET that
established an account would let an unfurl consume the invitation. An unfurl
fetches this page's *HTML* and executes none of its JavaScript, so no preview
reaches `/invitation` or `/accept`. The click that is skipped for an
administrator is skipped by a real browser running the application — which is a
person opening their own link, and nothing else.

**The read in front of the write.** `POST /api/auth/invitation` answers who a
link belongs to, what role it grants, and whether they already have an account.
It writes nothing: no account, no claim on the roster entry, no session. That
property is what makes it safe to call on page load, and it is asserted directly
— two lookups, then a check that no user exists, the entry still reads as
waiting, and the link still works.

It is unauthenticated, like `/accept` and for the same reason: the caller has no
session, and the token is what stands in for one. It tells the holder of a link
their own address and their own role, and holding the link already *is* being
that person (D29) — so it discloses nothing they do not have. To anybody without
a valid token it discloses nothing at all, with one message for never-existed,
revoked and replaced.

**A POST for a read**, which is worth naming because it looks wrong. The token is
a live credential; a GET would write it into the query string of every access log
between the browser and the application. The body keeps it out of them.

**The effective role, not the roster's promise.** The lookup reports the
*account's* role when there is an account, and the entry's only when there is
not. A colleague promoted last week under People must not be sent back to an
accept screen because the entry that let them in still says `member`. Re-opening
a link never *writes* a role either way — that hole is pinned by a test, because
"roster edits do not touch existing accounts" would otherwise be true only until
the person next opened their own link.

**Entering is for a browser with no session.** If somebody is already signed in,
nothing is spent on their behalf: their own link would take them where they
already are, and somebody else's must never silently swap one live session for
another. An administrator holding a colleague's link is shown the accept screen
with both addresses on it — because rendering the dashboard would be *correct*
and would also swallow the link, leaving them to conclude the feature works when
they have not seen it.

### Only an administrator can change a role, and that is now tested

The rule was already true of every endpoint that writes a role. It had no test of
its own, which is the same gap the sign-in gate shipped with: dropping
`require_admin` from one decorator would have gone through green, and the failure
mode is not a broken page, it is a member promoting themselves.
`tests/test_roles.py` enumerates every way a role can be written —
`PATCH /users/{id}`, `PATCH /roster/{id}`, `POST /roster`, the link endpoints,
and the three doors that have no `require_admin` to lose (`PATCH /me`,
`POST /register`, `POST /accept`, none of which read a role from the caller).
Each refusal is checked against the database rather than the response body: a 403
with a silently applied write behind it is exactly the bug worth catching.

A member sees a sentence where an administrator sees the workspace panels, rather
than empty space. Somebody told to "change so-and-so's role" who finds nothing
cannot tell whether the feature is missing, broken, or not theirs.

### The role is settled before a link exists

**`role` is required on `POST /api/auth/roster`, with no default.** It used to
fall back to `member`, which was harmless while the role only decided what an
account would be. Now it decides where the link *lands* its holder, so a request
that does not name one is asking for a link whose behaviour nobody chose. The
panel matches: the segmented control starts unset and the button stays disabled
until it is pressed.

**Re-roling somebody who has not joined revokes their link.** A link already
delivered would otherwise start behaving differently from the one the
administrator described when they sent it — same URL, different landing. Revoking
makes that visible: the row shows no link, says why, and issuing a new one is the
deliberate act that says "this is now an administrator's link". It is also the
mechanical form of the ordering this record asks for — addresses and roles first,
links second.

Left alone once they have joined, in both directions: a roster role never moves
an existing account, so revoking there would be a lockout in exchange for
nothing, and their link is their only credential. Setting the role it already has
is not a change and does not cost the link, or a panel that re-sent the current
value on any edit would quietly invalidate everybody's.

**What this costs.** An administrator who flips a role after sending a link has
to send a new one. That is the intended cost: the alternative is a link whose
meaning changed under its holder.

### What the review of this record changed

Written down because each of these was a real hole, not a tidy-up:

* **Spending a link on load makes a concurrent first accept reachable without
  anybody clicking twice** — two tabs, or a browser prerendering the URL from the
  address bar and running its JavaScript. Both requests read no account, both
  insert, and `users.email` is UNIQUE. `accept_access_link` now catches
  `IntegrityError`, rolls back and re-reads the winner's row. Note what this says
  about D29's unfurl argument: it covers link *previews*, which execute no
  JavaScript, and it does **not** cover prerendering, which does. The consequence
  is bounded — a prerender can only sign in whoever already holds the link — but
  the collision was not hypothetical.
* **The screen for somebody else's link had one button and it was the wrong
  one.** An administrator checking a colleague's link could only sign themselves
  out of their own account and into it. There is now a way to put the link down.
* **`/accept` and `/invitation` are the only endpoints reachable with no
  session**, and both took a string of any length. Capped at
  `MAX_TOKEN_LENGTH`: a real token is 43 characters in a `String(64)` column, so
  anything longer cannot match a row.
* **The lookup ran after the session call rather than beside it**, which put the
  invited person behind two round trips of blank frame on the one journey this
  whole record is about. Split into a read and a decide, in parallel.
* **`authApi.invitation` had no test of the request it makes.** Every test of the
  landing behaviour mocks that method, so a wrong path or a GET would have left
  the suite green and broken the feature for exactly the people who cannot report
  it — because they cannot get in.
* **The roster panel had no test at all**, and it is where the "roles before
  links" sequence is enforced. `WorkspaceRoster.test.tsx` covers it, and both
  halves were mutation-checked: restoring the `member` default and disabling the
  revoke-on-re-role each turn tests red.

---

## D31 — Every account can have a password, because signing out was a lockout

**Decision.** An account may hold a password *and* an access link, and three
things can now put one there: an administrator creates the account with one
(`POST /api/auth/users`), an administrator sets one on an existing account
(`POST /api/auth/users/{id}/password`), or the owner sets a first one themselves
(`POST /api/auth/me/password` with no `current_password`). `UserOut.has_password`
reports whether an account has one, and the account page warns the accounts that
do not.

**This does not delete D29. It removes the word "only" from it.** The access link
is still the frictionless first entry, still durable, still the whole of what a
new colleague needs. What changes is that it is no longer the *sole* credential,
because being the sole credential is what made the failure below possible.

### The defect, as reported

Four steps, nothing exotic in any of them:

1. somebody opens their access link and lands in the dashboard (D29)
2. they press **Sign out**
3. the sign-in page asks for an email and a password
4. they have never had a password, so there is no answer they can give

Everything that could be done about it was outside the product: find the original
link in a chat history, or find somebody with a shell on the deployment host to
run `python -m app.accounts_cli reset-password`. The page did not say that, and
nothing anywhere showed that this account was one press away from that state.

**D29 said a permanent link was "the only shape where nothing else is needed
stays true past the first fortnight".** That was right about the link and wrong
about the sentence: it stayed true right up until somebody signed out, and
signing out is not an exotic act. The record reasoned carefully about a link
being *lost* and not at all about a session being *ended on purpose*.

### Why a password rather than something cleverer

Three alternatives were considered and rejected:

* **Remember the link on the device.** A long-lived cookie, or `localStorage`,
  so the sign-in page could offer "continue as you". It works, and it makes
  **Sign out** a lie on a shared machine — the one place the button matters most.
  Splitting it into "sign out" and "sign out and forget this device" is a
  distinction nobody reads before clicking.
* **Never sign them out.** Hiding the button for passwordless accounts. Removes
  the one control somebody needs when they are on somebody else's laptop.
* **Email them their link.** D27 rejected mail transport, and reviving it to fix
  a lockout would make signing back in depend on a mail relay being up.

A password is the boring answer, and it is the one the person asking for this
asked for: *"whenever they log out, they can log in through this password and
mail ID."*

### The three doors, and what each one refuses

**`POST /api/auth/users`** — administrators only. The fourth way an account can
come into existence, and the only one an administrator drives end to end; the
other three all hand the password decision to somebody else. It starts **no
session** and returns no cookie, because the administrator is creating somebody
*else's* account and a cookie here would sign them in as that person. A duplicate
address is `409`, not an overwrite — otherwise "add this person" silently becomes
"reset their password".

**`POST /api/auth/users/{id}/password`** — administrators only, and the remedy
for anybody already stranded. **Every session of theirs ends, including the one
they may be reading on**, which differs from the self-service change on purpose:
the administrator cannot know which of somebody's sessions is the one that needed
the reset, and sparing the target's own browser would make a reset performed
because of a suspected compromise decorative. The count comes back so the panel
can say what it did to them.

**`POST /api/auth/me/password`** — `current_password` is now optional, and
**which case this is comes from the stored hash, never from what the caller
sends.** That ordering is the whole of the security argument. Written the other
way round — "no current password sent means none is needed" — anybody holding a
stolen session could rotate the password and own the account outright.
`test_an_account_with_a_password_still_has_to_prove_it` pins it, and the
mutation that inverts the condition turns it red.

Sending a `current_password` for an account that has none is refused rather than
ignored: somebody typing into that box is describing a belief about their own
account, and quietly accepting it teaches them the wrong thing.

**The empty-hash guard from D29 is what makes all of this safe to write.**
`verify_password` refuses an empty *stored* hash first and unconditionally, so
"this account has no password" can never be satisfied by submitting an empty one.
D31 adds a password form to exactly the accounts with an empty hash, which puts
the reflex to loosen that check closer to hand than it has ever been. Do not.

### What the pages say now

`has_password` exists so the interface can stop being silent about the state that
caused this. A passwordless account sees an amber panel saying that signing out
would lock it out and a form with no impossible field in it; the administrator's
**People** list marks those accounts **No password** and offers **Set password**
beside each; and the sign-in page's footer names the two ways back for somebody
who has already signed out.

The new-account form shows the password **in the clear as it is typed**. This is
an administrator dictating a credential to a colleague, and a masked field they
cannot read back is how a typo becomes a support request. Nothing is emailed;
delivery stays a deliberate act, as it is for an access link.

### What this costs

An administrator now hands out a password as well as, or instead of, a link — one
more thing to deliver. And a password that an administrator chose is a password
somebody else knows, until the owner changes it. Both were accepted against the
alternative, which is a colleague who cannot get back into the tool because they
pressed the button that says **Sign out**.

---

## D32 — One account is a fixed point, and it is named by the deployment

**Decision.** `PLATFORM_ADMIN_EMAIL` names one address that cannot be demoted,
cannot be deactivated, cannot change its own email, and cannot be taken off the
roster. Empty by default. Enforced in `accounts.is_platform_admin` and the four
guards that call it (`set_role`, `set_active`, `update_profile`,
`roster.remove_entry`), and pinned by `tests/test_platform_admin.py`.

**This is a different axis from the last-administrator guard, and conflating
them is the easy mistake.** That guard protects the *deployment*: it refuses to
remove the final way back in, so nobody can strand the workspace with one click.
It says nothing about *which* administrator survives. With three admins,
`admin_count > 1` holds and any of them may demote or deactivate any other —
including the person who set the deployment up. D32 protects a person; the
existing rule protects the installation. `test_the_guard_holds_even_with_other_administrators_present`
is that distinction written as an assertion.

**A deployment variable, not a database column.** This was the whole decision,
and the reasoning is the same one that makes `require_sign_in` a switch:

> An account nobody can remove is also an account nobody can revoke if it is
> ever compromised.

A protection that can only be lifted with database access, or worse a code
change, turns a stolen password into an incident that outlasts the deploy queue.
As a platform variable it is removable in seconds by whoever controls the
deployment, without a migration and without a release — and the people who
control the deployment are already the people who could edit the database
anyway, so nothing is weakened by making it fast.

A column was the alternative and was rejected on that ground alone; it is
stronger against an attacker holding only a dashboard session, and worse in the
case that actually matters.

**The email guard is the one that is easy to miss.** Three of the four refusals
are obvious. The fourth is that the protected account may not change *its own*
address: the guard is keyed on the address, so a self-service edit at
`PATCH /api/auth/me` would silently switch it off. It is also the change least
likely to be noticed, because nothing about that person's session would look
different afterwards. Refused rather than followed — moving the platform
administrator is a deployment change, so it happens in the deployment.

**Refusals are one-directional.** The guard blocks removal, never repair: the
protected account can still be promoted, reactivated, and renamed. A rule that
also blocked restoring the account would turn a mistake into a permanent one.

**A malformed variable protects nobody, not everybody.** `is_platform_admin`
returns False when the value will not normalise, so one typo cannot produce a
workspace in which no account can be administered at all. Matching runs through
`normalise_email` on both sides, so case and stray whitespace in the variable
still protect the account they were meant to.

**Accepted:**

* **It protects a role, it does not grant one.** Naming a member here does not
  make them an administrator; it only stops them being changed. Granting is
  still `PATCH /api/auth/users/{id}`, and doing both automatically would mean a
  variable that silently escalates an account on the next restart.
* **There is exactly one.** A list would need an ordering story for what happens
  when they disagree, and nothing here is asking for two.
* **Nothing in the dashboard says an account is protected.** The refusal arrives
  as a 403 with a sentence when somebody tries, which is the moment it matters.
  A badge in the team list would be a nicety; it is not what stops the click.

## D33 — A foreign notice is translated on request, by a keyless third party

Two thirds of the corpus is English. The other third is mostly Brazilian
Portuguese from PNCP — 470 notices with a description — and a reader who opens
one gets a wall of text they cannot judge. Scoring already worked on it
(`relevance.py` matches keywords in the original), so the gap was never
relevance; it was that a human could not read what the engine had ranked.

**Translation happens when somebody asks, not at ingest.** A notice is never
edited (D1), so the English text lives in its own table rather than in a column
on `tenders`, and it is written the first time a reader presses **Translate**.
Translating all 470 on the chance somebody opens one would spend far more than
it saves; the button is the signal that this notice, now, is worth it.

**The provider is Google's keyless `translate_a/single` endpoint, chosen
knowingly.** It is undocumented, unversioned, rate-limited by IP and has no
support path. The alternatives were a keyed service — Anthropic or DeepL, both
better on this kind of concatenated legal prose — and both need an account and a
key this deployment does not have. The keyless endpoint was accepted because it
works today with no setup, and because the cost of it breaking is one button
reporting a 502, not a broken sweep or a lost notice.

So the shape matters more than the choice: `translator.translate` is the only
function that knows a provider exists, `_PROVIDERS` maps a name to an
implementation, and `TRANSLATION_PROVIDER` selects one. Swapping to a keyed
service is an entry in that dict and a changed variable — no call site moves.

**The cache is the cost control, and there is deliberately no cooldown.** Unlike
`POST /api/fetch` (D23), a translation is something a person does while reading,
in bursts, and the same notice is only ever fetched from the provider once — the
unique constraint on `(tender_id, target_language)` enforces that in the
database rather than by checking first and hoping, so two people pressing the
button at the same instant produce one row and one outbound request.

**The server decides whether to offer the button; the browser only renders it.**
`language` is stored inconsistently — `en`, `eng`, `English`, `pt` and `French`
are all real production values, because each connector normalises differently
and the connectors are frozen. `TenderDetail.needs_translation` resolves that
once, next to `normalise_language`. A second normaliser in TypeScript would
have drifted the first time a feed changed what it emits, and the symptom would
be a missing button rather than an error.

**An unrecorded or unrecognised language is left alone, not guessed at.** A
button that sends English to a translator, or picks Portuguese for a notice that
is actually Spanish, produces confident nonsense — worse than no button, because
the reader cannot tell.

**The original is always one click away, and the copy says a machine wrote it.**
The English replaces the description in place rather than sitting beside it —
two columns of the same text is how a panel becomes unreadable — but **Show
original** restores it without another request, and the caption says
"Translated … by machine — read the original notice before relying on it". A bid
decision must not rest on an unattributed machine translation.

### D33 amended, same day — the chosen provider does not work in production

Shipped with `google_free` and it answered **HTTP 429 from Railway's egress IP**
on the first real call, with Google's datacenter abuse page as the body. A
browser `User-Agent` changed nothing, which is the tell: the block is on the
address, not the request. It works perfectly from a laptop, which is exactly why
the local suite, the local end-to-end run against PostgreSQL and a real 5,135
character translation all passed before deploying. **Nothing was wrong with the
code; the environment it now runs in is not the one it was proved in.**

The lesson is narrower than "test in production" and worth keeping: when a
dependency is chosen *because* it needs no account, the thing it screens on is
the caller's IP, so it must be exercised **from the deployment's own network**
before the feature is called done. Prod egress is not a detail of the provider,
it is a property of it.

`mymemory` is now the default. It answers from Railway, and it brings two
properties the shape had to absorb rather than hide:

1. **It reports failure inside an HTTP 200**, exactly like Slack's Web API (D22)
   — `responseStatus: 403` in the body with a 200 on the wire. Trusting the
   status code would have stored the string `QUERY LENGTH LIMIT EXCEEDED. MAX
   ALLOWED QUERY : 500 CHARS` as a notice's English description, and the cache
   would have kept it for ever. This is the second time this exact trap has
   appeared in this codebase, from an unrelated vendor.
2. **500 characters a request, hard.** So the per-request limit is now a
   property of the *provider* (`MAX_CHUNK_CHARS_BY_PROVIDER`) and caps the
   operator's setting rather than trusting it — `TRANSLATION_MAX_CHUNK_CHARS`
   above 500 with this provider would 403 every notice over 500 characters and
   read as "the service is broken".

Its two exhaustion messages share no keyword — `QUERY LENGTH LIMIT EXCEEDED` for
an over-long request and `YOU USED ALL AVAILABLE FREE TRANSLATIONS FOR TODAY` for
the daily allowance — and matching only `LIMIT` told somebody out of quota to
"try again in a minute", which would never work. They are now distinct messages
because they call for different actions: wait a day, or configure a key.

**The remaining constraint is honest and small:** 5,000 characters a day per IP
keyless, 50,000 with `TRANSLATION_CONTACT_EMAIL` set (an ordinary address, not a
credential). At an average description of 838 characters that is roughly six
notices a day, or sixty with the address. The per-notice cache is what makes
that workable at all — a notice is translated once for all time, not once per
view. If it becomes limiting, the fix is the path this was built for: a keyed
`_PROVIDERS` entry, no call site moved.

## D34 — The text decides what language a notice is in, not the column

D33 built the Translate button on the `language` column and wrote down the rule
that made it fail: *"An unrecorded or unrecognised language is left alone, not
guessed at."* That rule assumed the column is either right or empty. For the
largest source in the corpus it is neither — it is confidently wrong.

**The measurement.** 413 notices fetched live from all seven reachable sources
on 2026-09-01, every description classified and compared against the stored
value:

| source | notices | stored `language` | had a button | actually foreign |
|---|---|---|---|---|
| ted | 15 | `eng` on **100%** | **0** | **11** (9 German, 2 French) |
| pncp | 112 | `pt` | 112 | 112 ✓ |
| canada_buys | 220 | `en` | 0 | 1 (bilingual) |
| find_a_tender | 51 | `en` | 0 | 0 ✓ |
| world_bank | 8 | `English` | 0 | 0 ✓ |
| contracts_finder | 6 | `en` | 0 | 0 ✓ |
| austender | 1 | `en` | 0 | 0 ✓ |

**Why TED stores `eng` on every notice it has ever published.** TED
machine-translates a notice's *title* into all 24 EU languages, so
`notice-title` always contains an `eng` key; `TedConnector._normalize` reads the
notice's language off that map. The `description-lot` map carries only the
buyer's own language — `['deu']`, `['fra','nld']`. The stored value therefore
describes the title accurately and the description not at all, and
`needs_translation` compared it against the description. Nobody in Europe could
read a German notice, and the dashboard gave no sign anything was wrong: a
missing button looks exactly like a notice that does not need one.

**So the language of a description is now read from the description.**
`app/services/language.py` classifies the text with py3langid. This is a read-side
correction on purpose — the connectors are frozen (CLAUDE.md), and reading it at
request time also repairs every notice already stored, with no migration, no
backfill and no re-ingest.

**The stored value still wins when it names a foreign language.** PNCP says `pt`
on 112 of 112 and is right every time, and a stored code beats a classifier at
telling Portuguese from Spanish on two lines of boilerplate. Nothing in the
corpus was found claiming a foreign language it was not in, so there is no
measured reason to second-guess that half. It is only a stored *English* — a
claim the text can contradict — and a stored *nothing* that get checked.

**How much evidence it takes to contradict the feed depends on whether the feed
said anything.** Overturning a positive claim of English needs a confident
classification; filling in a missing one does not. Three English content words
carry almost no signal — `Cloud storage framework.` classifies as Dutch at 0.33 —
so without the asymmetry the button would appear on every short English notice.
It costs nothing measurable: every genuinely foreign description in the corpus
scored **1.00**, including the two-word German ones.

**Lowercase before classifying.** py3langid is trained on natural-case text, and
ALL-CAPS English reads as Maltese (0.91) and Xhosa (0.64) — two real CanadaBuys
notices did exactly that. Capitalised headers are the house style of procurement
writing, so this is the common case here, not an edge case. It was the difference
between four wrong classifications and none.

**D33's actual warning survives, narrowed.** "A confident translation of the
wrong thing" is still the failure to avoid — but the answer is not to withhold
the button, it is to stop inventing a source language. Below the confidence
threshold the provider is asked to detect the language itself (`langpair=
Autodetect|en`, which returns what it found), and if nobody can name it the API
sends an empty `source_language` and the dashboard says "translated from another
language by machine". A reader is told what is known and not told what is not.

**What this costs.** One notice in 286 English ones gains a button it does not
need, and that one is genuinely bilingual English-and-French. Against 11 of 15
TED notices that could not be read at all, that is the right side of the trade —
an unnecessary button costs a click, a missing one costs the reader the notice.

**Pinned by a sample of production, because invented fixtures could not catch
this.** `tests/fixtures/language_gold_set.json` holds 33 real notices with their
real stored `language`. Every hand-written fixture in the suite had a language
that was either correct or absent, so "trust the column" passed all of them;
TED's is neither, and no fixture had ever been shaped like that.

### D34 amended, same day — "not English" and "contains no English" are different questions

Deployed, and then measured against the 1,123 stored notices rather than the
413 fetched live. TED went from 0 buttons to **44 of 55**, which is the fix
working. CanadaBuys went from 0 to **127 of 256**, which is not.

Those 127 are **bilingual**: the English, a blank line, then the same text in
French. Classified whole they come back **French at 1.00** — French carries more
signal per character than English does, so it wins a 50/50 text outright. Every
one of them opens with English the reader can already read, and every one grew a
Translate button it did not need.

**The live sample could not have caught this.** It was a seven-day window of
*new* notices and happened to hold almost none of them: 1 of 220, against 127 of
256 in the stored corpus. A sample of production is better than an invented
fixture — that was D34's lesson — and a sample of *one week* of production is
still a sample.

So the question the button asks is now the right one. It was "what language is
this?"; it is "is there English here the reader can already read?". A notice is
left alone when a real share of it is English, whatever the whole-text
classification says.

**The share is measured over paragraphs and weighted by length.** Paragraphs
because that is the structure bilingual notices actually have — a blank line
between the two languages — and length-weighted because a French notice under a
two-word English header (`NOTICE OF PROPOSED PROCUREMENT`) is a French notice,
and counting segments would let that header outvote three thousand characters of
French. A paragraph over 600 characters is sub-split into windows so a bilingual
notice written as one unbroken block is caught too; below that it is left whole,
because chopping up `Küchentechnik Wartung` to look for English inside it would
only manufacture noise.

**The threshold is 0.15, and it was read off the data rather than chosen.**
Across the stored corpus the genuinely foreign notices — 44 TED, 477 PNCP — sit
at an English share of **0.00 exactly**, while bilingual CanadaBuys notices
spread from 0.16 to 0.67. Every threshold from 0.10 to 0.20 splits that corpus
identically; 0.15 is the middle of the plateau. A number with a plateau under it
is a number the data supports, not one fitted to it.

Effect on production: **canada_buys 127 → 0, ted 44 → 44, pncp 477 → 477.** The
two remaining single notices elsewhere are a genuinely Welsh Find-a-Tender
notice and a genuinely French World Bank one, both checked by hand.

One known miss, left alone deliberately: the Welsh notice is bilingual
Welsh/English in a *single* paragraph under the split threshold, so it keeps a
button it does not need. It is one notice in 1,123, the cost is a spare button,
and the fix — sub-splitting short paragraphs — would put the two-word German
notices at risk to save it.

## D35 — The translation provider is keyed, because a keyless one rations by IP

D33 chose a keyless provider knowingly and wrote down the cost: *"5,000
characters a day per IP keyless, 50,000 with `TRANSLATION_CONTACT_EMAIL` set...
At an average description of 838 characters that is roughly six notices a day."*
Six notices a day is the whole ration for the whole deployment, and
`TRANSLATION_CONTACT_EMAIL` was never set in production, so it was six and not
sixty. Production spent it, and the button answered

> The free translation service has used its daily allowance. Try again
> tomorrow, or configure a provider with a key.

to a reader who had pressed Translate once. The error handling was correct — the
message says exactly what happened and what to do — and that is the point: the
feature worked as designed, and the design was wrong. A ration measured in
notices-per-day is not a rate limit on a busy system, it is a feature that works
in testing and not in use.

**A keyless service can only ration by the caller's address.** That is not an
implementation detail to be tuned around; it is what "keyless" costs. Raising
the allowance to 50,000 with a contact address moves the ceiling to about sixty
notices a day, which is a better number and the same shape of problem. The only
thing that removes the ceiling is a key.

**DeepL, with a Pro key.** It reports `character_limit: 10^12` against a period
usage of 3.3M, which is not a ceiling anybody in this product will meet. D33
named DeepL and Anthropic as the two keyed alternatives and preferred them on
quality for this kind of concatenated legal prose; that judgement held up when
measured against the same German notice:

| provider | English |
|---|---|
| mymemory | "The subject of this tender is the floor coating work for the above-mentioned construction project (subsurface preparation by shot blasting as well as floor and wall coatings with 2-K epoxy resin and PU acrylate)." |
| deepl | "The subject of this invitation to tender is the floor coating work for the above-mentioned construction project (substrate preparation by shot blasting, and floor and wall coatings using two-component epoxy resin and PU-acrylate)." |

**The swap cost what D33 promised it would cost:** one `_PROVIDERS` entry, one
changed default, and no call site moved. That is the whole return on having
written `translate` as the only function that knows a provider exists.

Three things about DeepL that the shape had to absorb rather than hide:

1. **The whole notice goes in one request.** `text` is an array and the response
   is one object per element, in order — so a long description still chunks on
   sentence boundaries, but the chunks travel together instead of costing a
   request each. The keyless providers loop; this one does not.
2. **It detects the source language itself, per element, and is better at it
   than we are.** `deepl` therefore *ignores* the source it is handed. It named
   `Küchentechnik Wartung` as German where `language.detect` called it Swedish
   at 0.9966 — the exact short-text weakness D34 recorded as a known limitation,
   closed here as a side effect. What gets reported to the reader is what DeepL
   actually translated from, never what this app guessed; getting that
   precedence backwards would caption a German notice stored by PNCP as `pt`
   with "translated from Portuguese", and a test pins the order.
3. **Failure is in the status code.** Worth stating out loud only because the
   two providers either side of it in the same file both answer HTTP 200 when
   they fail (D22, D33), so the reflex when reading that module is not to trust
   a status code. 456 is the quota, 403 the key, 413 the size.

**The key's suffix picks the host.** A key ending `:fx` is a free account and
belongs to `api-free.deepl.com`; anything else is Pro and belongs to
`api.deepl.com`. Sending one to the other's host answers **403 "Wrong
endpoint"**, which reads as an authentication failure and is not one — so the
host is derived from the key rather than configured separately and got wrong.

**The keyless providers stay.** A deployment without a key sets
`TRANSLATION_PROVIDER=mymemory` and accepts the ration; nothing about that path
changed. `deepl_api_key` joins `SECRET_FIELDS`, because a key in a log line is a
key in the logs — the lesson SAM.gov taught this codebase already.

**What did not change: the cache is still the cost control.** It mattered when
the provider was rationed and it matters now for a different reason — it is what
keeps a keyed provider's bill proportional to what somebody actually read,
rather than to what was ingested.

## D36 — HigherGov is a ninth source, and it refuses to run without a saved search

HigherGov aggregates SAM, DIBBS, SBIR, grants and state/local opportunities into
one feed, which is coverage `sam.py` cannot reach: the SAM bulk extract carries
federal contract opportunities only. Added as a ninth connector rather than
replacing SAM, so the two overlap on federal notices and dedupe on
`source_notice_id` like any other pair of sources.

**The API has no free-text search.** Not on the opportunity endpoint and not on
any of the other eighteen — the OAS lists no keyword, query or text parameter
anywhere, and `opportunity` does not even accept `naics_code`/`psc_code` the way
`contract` and `idv` do. The required-parameter error names the whole set:
`search_id`, `captured_date`, `posted_date`, `source_id`, `agency_key`,
`opp_key`, `version_key`.

**Unknown parameters are accepted and silently ignored, which is the actual
hazard.** Measured 2026-09-02: `q`, `search`, `keyword`, `keywords`,
`search_text`, `text`, `title`, `description` and `query` each returned HTTP 200,
and `q="safety data sheet"` and `q=zzzzznonsensequery12345` produced
*byte-identical* `opp_key` sets. A connector written the obvious way would
therefore appear to filter while pulling the raw firehose — no error, no warning,
just quietly wrong. `posted_date__gte` behaves the same way; a comma range on
`posted_date` answers HTTP 500.

**So the saved search is the only filter that exists, and it is required.**
`search_id` names a search built in the HigherGov web UI, whose `searchID` is
copied out of that page's URL; it carries Keywords, NAICS, PSC, Set Aside, Date
Due and Value Range. With `HIGHERGOV_SEARCH_ID` unset the connector reports
itself unavailable instead of falling back to a date scan. That refusal is the
decision, and the numbers are why:

| Measurement | Value |
|---|---|
| Base usage, every subscription | 10,000 records/month |
| Opportunities posted on one day (2026-08-25) | 5,538 |
| Unfiltered scan → monthly quota exhausted in | ~1.8 days |
| Relevance of 300 sampled unfiltered records | **0** reached the 50-point band |

The single record above 25 in that sample — "C--OSHA A/E Renovation Design and
Phasing Services" — was a false positive from the `OSHA` token on a
building-renovation contract. A fallback path here is not a degraded mode, it is
a way to spend the entire allowance on noise.

**One request per sweep, window applied client-side.** Because `posted_date`
takes a single date and rejects ranges, covering an N-day window server-side
would cost N requests. A precision-first saved search returns tens of records in
total, so the whole search is fetched once and filtered in code — which keeps a
sweep at one request whatever the lookback, and the monthly quota is what binds.
The window matches `posted_date` **or** `captured_date`: 15 of 55 live records
had them differ, because a notice posted weeks ago can be captured today, and
filtering on `posted_date` alone would drop it. `source_updated_at` is
`captured_date`, since for an aggregator "new to us" means when *it* saw the
notice.

**Prefiltered on title and buyer, never on the description.** The saved search is
a relevance filter tuned by whoever built it, not a topical guarantee. On the
live search this was verified against, the title+buyer prefilter cut 55 records
to 5 and lost no record scoring ≥50 — the 50 it dropped were all chemical
*purchase* notices (ADHESIVE, POTASSIUM NITRATE, Ice Melt, animal feed) whose
text merely requires an SDS on delivery. Same measured trade-off `sam.py`
records for the same reason, so the same choice.

**Verified against a real saved search, not a schema.** `searchID`
`OvSsysuZMmV1UnmB1s0hJ` returned 55 open records, 24 federal + 29 SLED + 2
forecast — so the `/sl/` path in a browser URL does *not* constrain
`source_type`, the search's own filters do. One record cleared the review band: a
Department of Energy / Idaho National Laboratory "Chemical Management Managed
Service", $1.5–5M, closing 2026-09-15, asking for administration of a Chemical
Inventory System with an SDS library. Precision at 1.8% is a property of that
search's keywords, which match the bare phrase rather than the product; the
strong tier ("sds management", "ehs software") is what belongs in it.

**The response carries the credential.** Every record's `document_path` arrives
with the caller's own `api_key=` embedded. Stored verbatim it would be written to
the database and rendered in the dashboard, which log redaction does not cover
because `raw_payload` is stored whole. It is scrubbed in the connector; see the
CLAUDE.md entry.

**A related defect this surfaced but did not fix.** 13 of the 14 records scoring
≥25 escaped `false_positives.chemical_purchase_sds_doc` entirely — the patterns
miss a plural subject ("vendors **are** required to provide"), a subject detached
from its verb, the source-text misspelling "saftey data sheets", "available upon
request", "submission of ... (sdss)", "documentation such as", and bare mentions
in goods spec lists. Eight added patterns cut the ≥25 band from 25.5% to 7.3%
while preserving the genuine hit. Not applied: `config/relevance_profiles.yaml`
is frozen core and scoring all nine sources, so it is a separate decision with
its own baseline SHA to re-pin.
