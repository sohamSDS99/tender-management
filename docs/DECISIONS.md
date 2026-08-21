# Architecture decisions

Decisions that are already implemented in this repository, with the reasoning that
produced them. Each record names the files that carry the decision, so a reader can
check the code rather than trust the prose. Verified against commit `7957480`.

Read D2 before enabling any scheduler, and D5 before making the API reachable from
anywhere other than the machine it runs on.

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

**Decision.** README section 12 states the API has no authentication. That stays true
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

## Not done, deliberately

* **No attachment download or parsing.** Documents are linked, never fetched, so their
  text is not scored. Unchanged from the baseline; README section 12 says so.
* **No rate limiting.** Read endpoints can be called without limit (D5).
* **No per-user authentication.** There are no user accounts; writes are gated by one
  shared secret (D5).
* **No cloud deployment.** The deployment is the local Compose stack (D1); Railway was
  rejected (D4).
* **No Neon, Slack or repository-secret provisioning.** Every config file, command and
  secret name is in place (`.env.example`, `docker-compose.yml`,
  `.github/workflows/scheduled-fetch.yml`), and `CRON_SECRET` can be generated locally,
  but creating a Neon project, a Slack incoming webhook or a GitHub Actions secret
  requires the user's own credentials.
