# Tender Monitor — SDS & EHS software opportunities

Finds public tenders that are worth bidding on for a **cloud-hosted SDS + EHS SaaS platform**
(SDS management, SDS authoring, SDS distribution, chemical/GHS compliance, EHS platform,
incident / inspection / audit management), and explains every score it gives.

* **Backend** — FastAPI + SQLAlchemy 2 + Alembic + APScheduler (Python 3.12; PostgreSQL in
  Docker, SQLite for local dev and the test suite)
* **Frontend** — React + TypeScript + Vite, plain CSS, one monochrome palette
* **Sources** — 8 built-in connectors over free public APIs / OCDS / CSV / RSS feeds, plus any
  feed you point at and map from the dashboard, without shipping a release
* **Relevance** — deterministic, YAML-driven, fully explainable. No AI service, no paid API.
  The phrases and weights people actually tune are editable in the dashboard.
* **Operating it** — a sweep, a re-score, the schedule, the source keys and the Slack
  destination are all driven from the dashboard. No accounts, internal network only.

```
┌──────────┐   fetch    ┌───────────────┐  normalize  ┌──────────┐  score  ┌──────────┐
│ 8 source │──────────▶ │  connectors   │───────────▶ │ upsert   │───────▶ │ REST API │──▶ React UI
│  feeds   │  httpx     │ (retry/429)   │  common     │ + hash   │  YAML   │ /docs    │
└──────────┘            └───────────────┘  model      └──────────┘  rules  └──────────┘
```

---

## 1. Quick start with Docker (one command)

> Operating this deployment: `docs/RUNBOOK.md`. Running the demo: `docs/DEMO.md`.
> Why it is built this way: `docs/DECISIONS.md`.


```bash
cp .env.example .env          # optional: add SAM_GOV_API_KEY
docker compose up -d --build
```

Three containers: the dashboard (nginx serves the built SPA and proxies `/api` and `/health`, so
the browser only ever talks to one origin), the API, and PostgreSQL on the internal network.

| What | URL |
| --- | --- |
| Frontend dashboard | <http://localhost:8080> |
| API | <http://localhost:8000> |
| Swagger UI | <http://localhost:8000/docs> |
| ReDoc | <http://localhost:8000/redoc> |
| Health | <http://localhost:8000/health> |

Both published ports are overridable — set `WEB_PORT` / `API_PORT` in `.env` when something else
already holds 8080 or 8000. **`PUBLIC_APP_URL` must agree with `WEB_PORT`**, or every link in
every Slack digest points at a port nothing is listening on. Read the port off `.env` or
`docker ps` rather than assuming the default.

Storage is PostgreSQL in a named volume, so the database survives `docker compose down` and any
rebuild. Migrations run automatically at container startup.

Load demo data (14 fixture tenders covering every score band, deployment class and
false-positive case) without calling any external API:

```bash
docker compose exec backend python -m app.seed --reset
```

Then press **Fetch** in the rail — it offers 3 / 7 / 30 / 90 days and states the window it
will search — or:

```bash
curl -X POST http://localhost:8000/api/fetch \
  -H 'Content-Type: application/json' \
  -d '{"sources": ["ted", "find_a_tender", "contracts_finder"], "days_back": 3}'
```

A full sweep across every source takes around 13 minutes against eight public services. To prove
the endpoint path works, fetch one cheap source instead (`austender` returns in about a second) —
it runs the identical guard and ingest code.

## 2. Local development

### Backend

```bash
cd backend
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt

cp ../.env.example ../.env                # optional
alembic upgrade head                      # create/upgrade the schema
python -m app.seed                        # optional demo data
uvicorn app.main:app --reload --port 8000
```

### Frontend

```bash
cd frontend
npm install
npm run dev            # http://localhost:5173 (proxies /api and /health to :8000)
```

The frontend always talks to the API through **relative URLs**: Vite proxies them in
development, nginx proxies them in the Docker image. Point it somewhere else with
`VITE_API_BASE_URL=https://api.example.org npm run dev`, or change the proxy target with
`VITE_PROXY_TARGET=http://localhost:9000 npm run dev`.

### Tests, linting, formatting

```bash
# backend
cd backend
pytest -q                    # 457 tests, no network access required
ruff check .                 # lint
ruff check . --fix           # autofix
ruff format .                # format

# frontend
cd frontend
npx vitest run               # 106 tests
npm run lint                 # tsc --noEmit
npm run build                # type-check + production build
npm run format               # prettier
npm run format:check
```

`tests/test_relevance_baseline.py` pins the engine's output for the 14 seed fixtures to a SHA-256
at a frozen instant. If it fails, the scoring changed — do not regenerate the hash to make it
pass unless that was the deliberate intent. Tests never read the wall clock, for the same reason:
a date-window test that called `utcnow()` started failing the day after it was written.

### Database migrations

```bash
cd backend
alembic upgrade head                                  # apply
alembic revision --autogenerate -m "describe change"  # create after editing app/models
alembic downgrade -1                                  # roll back one step
alembic current                                       # where am I
```

Six revisions, head `9ad56685baa8`; they come up clean from empty on both SQLite and
PostgreSQL, and CI proves it on both.

`app.db.init_db()` runs `alembic upgrade head` on startup (disable with
`RUN_MIGRATIONS_ON_STARTUP=false`). `psycopg[binary]` is already pinned, so PostgreSQL needs
nothing installed and no code change:

```bash
export DATABASE_URL=postgresql+psycopg://tender:tender@localhost:5432/tenders
alembic upgrade head
```

Every stored datetime is **naive UTC**. Dhaka is presentation and scheduling only; never write an
aware datetime to the database.

## 3. Getting the free SAM.gov API key

SAM.gov is the only source that needs a credential, and it is free:

1. Create an account at <https://sam.gov> (Entity registration is **not** required).
2. Sign in, open **Account Details → API Keys**, and request a *public* API key
   (<https://sam.gov/content/api-keys>).
3. Either put it in `.env` (`SAM_GOV_API_KEY=xxxxxxxx`) and restart, or paste it into
   **Settings → Sources** in the dashboard, which stores it in `app_settings`, beats the
   environment variable, and applies on the next sweep without a restart.

Without the key, the connector reports `unavailable` in `/api/sources` and on the source card,
its runs are recorded with status `skipped`, and every other source keeps working.

Keys are **write-only** over the API: `/api/sources` returns a `credential_hint`, never a stored
value. Nothing about reading a secret can be rate-limited, so that read path does not exist —
see section 6.

## 4. Sources and their limitations

| Source | Endpoint / feed | Auth | Pagination | Notes and limitations |
| --- | --- | --- | --- | --- |
| **EU TED** | `POST https://api.ted.europa.eu/v3/notices/search` | none | iteration token | Expert-search full-text query (`FT ~ "…"`) over `publication-date`. TED applies language stemming, so some hits are only loosely related — the relevance engine filters them. Stage is derived from the notice-type code (`pin*`→planning, `cn*`→tender, `can*`→award). |
| **US SAM.gov** | `GET https://api.sam.gov/opportunities/v2/search` | **free key** | `limit`/`offset` | `ptype=o,p,k,r` (solicitation, presolicitation, combined, sources sought). Descriptions live behind a per-notice link and are only fetched for notices that pass the topical prefilter (≤60 per run). Estimated values are not published by this API. |
| **UK Find a Tender** | `GET .../api/1.0/ocdsReleasePackages` | none | `links.next` cursor | `updatedFrom`/`updatedTo`. Planning, tender and award releases are all captured; tender stage is the primary opportunity. No server-side keyword search → local prefilter. |
| **UK Contracts Finder** | `GET .../Published/Notices/OCDS/Search` | none | `links.next` cursor | `publishedFrom`/`publishedTo`, `stages=tender,planning`. Stores the OCDS id and the source notice id. No server-side keyword search → local prefilter. |
| **World Bank** | `GET https://search.worldbank.org/api/procnotices` | none | `os`/`rows` | Uses the documented `qterm` keyword parameter (one request per phrase, see `app/connectors/world_bank.py`). Contract awards and drafts are dropped. Notices are kept when published in the window **or** still open for submission. No date filter exists on this endpoint, so the window is applied client-side. |
| **CanadaBuys** | `newTenderNotice-*.csv`, `openTenderNotice-*.csv` | none | whole file | Scheduled machine-readable CSV feeds; the new-notices feed gives frequent updates, the open-notices feed reconciles (≈7 MB, disable with `ENABLE_CANADA_BUYS_OPEN_FEED=false`). Bilingual EN/FR titles and descriptions are both stored. Closing times carry no timezone in the feed and are stored as-is. |
| **AusTender** | `https://www.tenders.gov.au/public_data/rss/rss.xml` | none | single feed | Current ATM list. The feed only publishes title, link, description and publication date — **no closing date, buyer or value**. XML is parsed with a DOCTYPE/ENTITY guard and a response-size cap. Requires a browser-shaped `User-Agent` (the default `USER_AGENT` satisfies this and CanadaBuys' WAF). |
| **Brazil PNCP** | `/v1/contratacoes/atualizacao`, `/v1/contratacoes/proposta` | none | `pagina`/`tamanhoPagina` (max 50) | Documented params only; `atualizacao` needs `codigoModalidadeContratacao`, so the connector iterates `PNCP_MODALIDADES`. PNCP has **no keyword search and very high volume** (>13 000 updates per 3 days per modalidade): coverage is capped at `PNCP_MAX_PAGES` pages per query and prefiltered locally. Raise `PNCP_MAX_PAGES` for fuller coverage. Portuguese text is preserved verbatim. |

**Never scraped:** every source above is an official API, OCDS feed, CSV or RSS feed.
No connector ever invents sample data — fixtures only enter the database through
`python -m app.seed`.

### Adding a source nobody anticipated

The eight above each parse one portal. `app/connectors/generic.py` parses whatever it is pointed
at, given a mapping from that portal's field names to the common model — which is what lets a
feed be added from **Settings → Sources** instead of in a release. Paths are dotted, with `[]`
marking an array to walk (`data.items[].tender.title`); deliberately not JSONPath, whose filters
and recursive descent a field-picker UI cannot present and which are only more ways for a mapping
to be subtly wrong. A missing path yields nothing rather than raising, because portals omit
optional fields on individual notices constantly and one sparse record must not fail a sweep.

`POST /api/sources/probe` tries the candidate before it is saved, and does two things that matter:

* **It refuses unsafe addresses.** This makes the *server* fetch a URL a dashboard user typed,
  and the dashboard is unauthenticated by design — so localhost, the internal network and the
  cloud metadata endpoint are blocked. That guard is a condition of the feature existing, not a
  hardening pass for later.
* **It reports what parsed, not what answered.** A `200` proves the credential works and nothing
  else. A source that answers while yielding no notices is exactly the failure this project spent
  a day chasing on SAM.gov, so the probe counts what came out and refuses to call an empty result
  a success.

A user-defined source cannot take a built-in's name, and only sources somebody added can be
deleted.

### The keyword prefilter

Sources with no server-side keyword search would otherwise store an entire national tender
feed (Find a Tender alone publishes ~450 notices/day). Those connectors apply a deliberately
broad topical prefilter (`backend/app/connectors/keywords.py` → `PREFILTER_TERMS`: chemical,
hazard, safety, EHS/HSE, incident, inspection, audit, risk assessment, plus DE/FR/ES/PT
equivalents) before storing anything. Everything that passes is stored **and scored**, including
low scores, so you can audit the checker's decisions. Set `APPLY_KEYWORD_PREFILTER=false` to
store everything in the window instead.

## 5. Relevance and product-fit scoring

All keywords, weights, patterns and caps live in **`config/relevance_profiles.yaml`** — edit it
and press **Re-score** in the UI (or `POST /api/tenders/rescore`) to reprocess everything.

The tunable part of that file is also editable from **Settings → Matching rules**: capability
phrases as a paged table, the capabilities themselves add- and removable, and a preview of what
a change would move *before* it moves anything. The file is never rewritten — overrides live in
`app_settings` and are merged over it at load. That keeps the file authoritative and its
load-bearing comments intact (they document the matching contract every phrase has to satisfy),
makes "reset to defaults" a row deletion, and adds no comment-preserving YAML writer as a
dependency. The `patterns:` regexes stay in the file: they are the sharp edge and are rarely
touched.

```
final score = 0.55 × topic relevance      (capability phrases, title weighted ×1.9, modules, codes)
            + 0.30 × product & deployment  (cloud/SaaS signals + deployment classification)
            + 0.15 × procurement intent    (software-purchase language, licensing, implementation)
```

Then hard caps and non-actionable multipliers are applied. Every tender gets:

| Field | Meaning |
| --- | --- |
| `relevance_score` | 0–100 |
| `relevance_category` | best matching profile: `sds_management`, `sds_authoring`, `sds_distribution`, `chemical_compliance`, `ehs_platform`, `incident_management`, `inspection_management`, `audit_management` |
| `fit_status` | `high_fit` · `good_fit` · `possible_fit` · `manual_review` · `not_fit` |
| `deployment_fit` | `cloud_required` · `cloud_preferred` · `cloud_allowed` · `deployment_unspecified` · `hybrid` · `mandatory_on_premises` · `offline_or_air_gapped` |
| `relevance_reasons` | human-readable reasons ("Cloud / SaaS explicitly required: 'must be cloud based'") |
| `disqualifiers` | why it is not a fit (mandatory on-premises, chemical purchase, PPE, …) |
| `review_flags` | why a human should look (hybrid hosting, private/government cloud, ambiguous "SDS", passed deadline) |
| `topic_relevance_score`, `product_fit_score`, `procurement_intent_score` | the three subscores |
| `is_actionable` | false when the deadline has passed or the notice is cancelled/awarded |

**Score bands:** 85–100 excellent · 70–84 good · 50–69 possible/manual review ·
25–49 weak · 0–24 not relevant. Scores 25–49 are reported as `not_fit` (not actionable) but
stay visible and fully explained.

### Hard caps (keyword matches can never override them)

| Situation | Max final score |
| --- | --- |
| Mandatory on-premises deployment | 20 |
| Offline / air-gapped requirement | 20 |
| Chemical/material purchase where an SDS is only a required delivery document | 15 |
| EHS consulting, training or staffing without software | 35 |
| Physical safety equipment / PPE | 10 |
| "SDS" used in an unrelated sense (software-defined storage, …) | 15 |

Expired or cancelled notices keep their topic score, are multiplied down (0.65 / 0.7) and
flagged `is_actionable = false`.

### False-positive handling

* `Supplier must provide an SDS for every chemical delivered` → capped at 15, disqualified.
* `Software defined storage (SDS) expansion` → disqualified; the acronym is gated on nearby
  chemical/hazard/GHS/REACH/authoring context.
* `REACH` only counts as the EU regulation when chemical/regulatory context is nearby
  (otherwise it is the English verb).
* Consultancy **is** kept when it implements, configures, operates or supports an EHS/SDS
  platform (`software_override_phrases` in the YAML).
* Both cloud and on-premises permitted → `hybrid` + `manual_review`, never auto-rejected.
* Private / government cloud, buyer-provided hosting → `manual_review` flag, never rejected.
* Deployment not mentioned → `deployment_unspecified`, **no penalty**.

### Example

```json
{
  "relevance_score": 97,
  "relevance_category": "sds_management",
  "fit_status": "high_fit",
  "deployment_fit": "cloud_required",
  "relevance_reasons": [
    "Title matches SDS management: 'safety data sheets'",
    "Requests 4 supported capability areas: SDS management, SDS authoring, Chemical and GHS compliance, SDS distribution",
    "Cloud / SaaS explicitly required: 'must be cloud'",
    "Procurement language indicates a software purchase: 'subscription licensing', 'implementation services'",
    "CPV code 48000000 (Software package and information systems) is a relevant classification signal"
  ],
  "disqualifiers": [],
  "review_flags": []
}
```

## 6. API

Interactive documentation: <http://localhost:8000/docs>.

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/health` | liveness + database check |
| GET | `/api/stats` | dashboard counters, distributions, filter option values |
| GET | `/api/tenders` | paginated, filtered, sorted list |
| GET | `/api/tenders/{id}` | full record incl. raw source payload |
| POST | `/api/tenders/rescore` | reload the profile and re-score every stored row |
| GET | `/api/sources` | every source: enabled, key required, prefiltered, tender count, last run/status/error |
| POST | `/api/sources` | save a user-defined source (refuses a built-in's name) |
| DELETE | `/api/sources/{name}` | remove a source somebody added |
| POST | `/api/sources/probe` | try a candidate feed, report what *parsed*, store nothing |
| PUT | `/api/sources/{name}/credential` | set or clear that source's API key — write-only |
| POST | `/api/fetch` | start a sweep; `202` with one queued run per source |
| GET | `/api/fetch-runs` | run history with per-source counters and errors |
| GET | `/api/automation` | next run in Dhaka time, last run's outcome, Slack health, the scheduler's actual registered jobs |
| PUT | `/api/automation/schedule` | set the times of day the sweep runs (1–6 local hours), D19 |
| PUT | `/api/automation/trigger` | switch automated sweeps on or off, D21 |
| GET | `/api/matching-rules` | the tunable part of the relevance profile, with its overrides |
| PUT | `/api/matching-rules` | store an override |
| DELETE | `/api/matching-rules` | reset to the file's defaults (a row deletion) |
| POST | `/api/matching-rules/preview` | what a rule change would move, without moving it |
| GET | `/api/settings/secrets` | which operator-settable values are configured — never returns one |
| PUT | `/api/settings/secrets/{field}` | set or clear the Slack destination — write-only |

`GET /api/tenders` query parameters: `query`, `sources`, `countries`, `categories`, `statuses`,
`fit_statuses`, `deployment_fits`, `minimum_score`, `maximum_score`, `published_from`,
`published_to`, `deadline_from`, `deadline_to`, `active_only`, `has_deadline`, `sort`
(`score_desc`, `score_asc`, `deadline_asc`, `deadline_desc`, `published_desc`, `published_asc`,
`first_seen_desc`), `page`, `page_size`. List parameters repeat: `?sources=ted&sources=sam`.

```bash
# highly relevant, cloud-compatible, still open
curl "http://localhost:8000/api/tenders?minimum_score=70&active_only=true\
&deployment_fits=cloud_required&deployment_fits=cloud_preferred&sort=deadline_asc"

# what the automation is doing
curl http://localhost:8000/api/automation

# start a sweep, then watch the runs (no secret needed; CI adds one to skip the cooldown)
curl -X POST http://localhost:8000/api/fetch \
  -H 'Content-Type: application/json' \
  -d '{"sources": ["austender"], "days_back": 3}'
curl "http://localhost:8000/api/fetch-runs?limit=8"
```

`POST /api/fetch` returns `202` immediately with one queued run per source, never waits for the
connectors, and refuses to start a source that is already running (returned in
`skipped_sources`). Progress and results are read from `/api/fetch-runs`.

### Nothing is gated on a shared secret

Reads are open by design — the content is public procurement data already published by
governments, and the tool is internal-network only with no accounts (D5, D18).

The two expensive writes were never gated for *confidentiality* either. They were gated because
one spends outbound requests against eight public services and the other rewrites every stored
row — that is cost control, and a secret is a poor cost control: it says who may ask, not how
often. Each now carries the limit it actually needed (D23), which is also what let the buttons
the mockup asked for exist at all:

| Endpoint | Limit | Why that limit |
|---|---|---|
| `POST /api/fetch` | single-flight (`409`) + a 300 s cooldown (`429`) | spends outbound requests against 8 public services |
| `POST /api/tenders/rescore` | a 120 s cooldown (`429`) | rewrites every stored row |
| `PUT /api/automation/schedule` | none | choosing a time costs nothing (D19) |
| `PUT /api/automation/trigger` | none | pausing spends less than doing nothing (D21) |

`X-Cron-Secret` still works and skips the cooldowns, because CI and the scheduled entrypoint
control their own timing. An unset `CRON_SECRET` no longer refuses these endpoints — it used to,
which was right while the secret was the only control. `ALLOW_OPERATOR_ACTIONS=false` closes
every write to the browser with `403`, and **must be set before the API is ever
internet-reachable**. A crashed sweep cannot brick the Fetch button: a `running` row older than
`stale_run_minutes` (60) is ignored when deciding whether a sweep is in flight.

Credentials are the one thing that inverts this. Nothing about reading a secret can be
rate-limited, so no endpoint returns one — the read path is closed rather than gated, and the API
exposes only a hint.

Prefer the CLI (`python -m app.jobs.scheduled_fetch`) over the endpoint for a complete run: it
does the whole thing, including the Slack digest, and exits with a meaningful code.

## 7. Data model

`tenders` is unique on `(source, source_notice_id)` and upserted, never duplicated:

* `content_hash` (sha256 over the meaningful fields) decides *created / updated / unchanged*
* `first_seen_at` never changes; `last_seen_at` is written on every observation
* all datetimes are stored in **UTC**; the original offset is kept in `source_timezone`
* `raw_payload`, `classification_codes`, `document_urls`, `relevance_reasons`,
  `disqualifiers`, `review_flags` are JSON columns
* attachment URLs are stored, files are not downloaded

`first_seen_at` being immutable is load-bearing, not incidental: it is the only reason "new in
this run" is computable without touching the frozen ingest path, and it is what both the Slack
digest and the New lens filter on.

`fetch_runs` records `source`, `started_at`, `finished_at`, `status`
(`queued`/`running`/`success`/`partial`/`skipped`/`failed`), `records_received`,
`records_created`, `records_updated`, `records_skipped`, `error_message`, the window used, and a
`batch_id` grouping the per-source rows of one sweep.

Three smaller tables carry everything an operator changes:

| Table | Holds |
| --- | --- |
| `sources` | a feed somebody added: its URL, auth style, field mapping and enabled flag |
| `app_settings` | the sweep times, the on/off decision, the matching-rule overrides, and the stored credentials — each beating its environment variable and applying without a restart |
| `slack_notifications` | the at-most-once delivery ledger, unique on `(tender_id, channel_label)` |

## 8. Automation, scheduling and reliability

Fetching is automated twice a day, and a sweep can also be started from the dashboard. What the
UI decides is when it runs, whether it runs at all, and how deep a sweep started by hand looks —
see **Operator controls** below.

**Schedule.** Two runs a day at **00:00 and 12:00 Asia/Dhaka** by default. Dhaka is UTC+6 with no DST, so
that is `0 18 * * *` and `0 6 * * *` in UTC. The conversion is computed from `ZoneInfo` in
`backend/app/jobs/schedule.py` and asserted in `tests/test_jobs_schedule.py`, which also checks
that `.github/workflows/scheduled-fetch.yml` still agrees with the code. The offset is never
hardcoded.

**Two possible trigger owners — enable exactly one:**

| Owner | How | When it fits |
|---|---|---|
| In-process APScheduler | `ENABLE_SCHEDULER=true` (docker-compose sets it) | the app runs continuously on one host — the local default |
| GitHub Actions workflow | add a `DATABASE_URL` secret | the database is reachable from the internet |

`Settings.enable_scheduler` defaults to **false** so no process ever fetches unexpectedly. Running
both against the same database fetches every window twice; see `docs/DECISIONS.md` D2.

**Operator controls.** Both of the scheduling decisions are editable from the dashboard, with the
environment variable kept only as the default. Neither needs a shared secret: the member of staff
making the change *is* the authorisation, on a tool with no accounts on an internal network
(D19, D21). Both are stored in `app_settings`, survive a restart, and are applied to the running
scheduler without one.

| Control | Endpoint | Environment default |
|---|---|---|
| **Sweep times** — 1–6 local hours a day | `PUT /api/automation/schedule` | `SCHEDULER_HOURS_LOCAL` |
| **Automated sweeps on/off** — pause and resume | `PUT /api/automation/trigger` | `ENABLE_SCHEDULER` |
| **A sweep now**, at 3 / 7 / 30 / 90 days | `POST /api/fetch` | `OPERATOR_FETCH_DAYS_BACK` |
| **A re-score of every stored row** | `POST /api/tenders/rescore` | — |
| **Each source's API key** | `PUT /api/sources/{name}/credential` | e.g. `SAM_GOV_API_KEY` |
| **The Slack destination** | `PUT /api/settings/secrets/{field}` | `SLACK_*` |

Because the stored value wins, the environment variable is only ever the default: a pause set in
the dashboard survives a restart, and so does a rotated key.

`scheduler_in_process` in `/api/automation` means "the decision in force", not `ENABLE_SCHEDULER`.
The dashboard's "switched on but not running" alarm keys on it, so reporting the environment
variable there would make a deliberate pause read as a fault (D21).

Pausing asks for confirmation, resuming does not, and a paused system says so in three places —
a banner at the top of the dashboard, the collapsed system summary, and the control itself with
the time it was paused. A paused system that looked healthy would be indistinguishable from one
that had simply found nothing. `docs/RUNBOOK.md` sections 5b and 5c are the operating procedures.

**The two sweeps search different windows, on purpose.** A scheduled sweep looks back 72 hours
(`FETCH_MIN_LOOKBACK_HOURS`, a catch-up overlap). A sweep started from the dashboard looks back
`OPERATOR_FETCH_DAYS_BACK` (30 days) unless a depth is chosen. They used to be the same, and that
was a reported bug: the button re-queried the window the last cron run had already emptied, so it
hit eight public services, created nothing, and reported success. Measured minutes apart on the
same five connectors — 34 notices over 72 hours, 119 over 30 days. The 72-hour floor still applies
underneath, so an operator sweep can never search *less* than the schedule does. If you ever see
"Fetch finds nothing", check the window before the connectors (D24).

**One run, one code path.** The APScheduler job, the CLI and the workflow all call
`run_once()` in `backend/app/jobs/scheduled_fetch.py`: window → fetch → score → notify.

```bash
# one complete run, by hand (safe to repeat at any time)
python -m app.jobs.scheduled_fetch --trigger cron

# re-run a missed window
python -m app.jobs.scheduled_fetch --trigger cron --days-back 7

# deterministic replay from the committed fixtures, for a demo
python -m app.jobs.scheduled_fetch --seed --seed-reset --trigger cron
```

Exit codes: `0` ingest and notification settled, `1` total ingest failure, `2` ingested safely but
the Slack digest failed. Logs go to stderr so `--json` keeps stdout machine-readable.

**Slack digests.** Each run posts one message for the tenders it *created* that score at or above
`SLACK_MIN_SCORE` (70) and are `is_actionable`. Every entry links to
`{PUBLIC_APP_URL}/?tender=<id>` — this system, not the source — with the original notice as a
secondary link. A run that finds nothing posts a one-line heartbeat, so silence is never
ambiguous. Delivery is **at most once per tender per channel, for all time**, enforced by a unique
constraint on `slack_notifications(tender_id, channel_label)` rather than by convention: a
retried, delayed or double-fired run cannot re-announce anything. A Slack failure never rolls back
ingested data — it is recorded, surfaced in the dashboard as a degraded state, and retried by the
next run.

**Two Slack transports.** The same payload is delivered either by `chat.postMessage` with a bot
token or by an incoming webhook. Which one is in force is *derived* from what is configured, never
set separately, so the configuration cannot claim a transport it does not have — and
`GET /api/automation` reports the one it resolved to:

| Transport | Configure | Notes |
|---|---|---|
| `bot_token` *(preferred)* | `SLACK_BOT_TOKEN` + `SLACK_CHANNEL_ID` | revocable, scoped, can post to any public channel with `chat:write.public` |
| `webhook` | `SLACK_WEBHOOK_URL` | the URL *is* the credential; cannot be rotated without re-issuing it |
| `none` | neither | the run reports `disabled` and names both ways to fix it |

The two differ in one way that matters more than it looks: the Web API answers **HTTP 200 with
`{"ok": false}`** on failure, so the status code alone is not success. Treating it as success would
write `sent` to the ledger and the unique constraint above would then stop that tender from ever
being announced — a silent, permanent loss. The body is authoritative; see `docs/DECISIONS.md` D22.

Only the bot token is needed to *send*. A Slack app also issues a signing secret, a verification
token and OAuth client credentials; those are for *receiving* from Slack — slash commands, events,
interactivity — which this product deliberately does not do, and which would mean exposing an
endpoint against D5/D18.

`SLACK_CHANNEL_LABEL` is the ledger key, not merely a label. Changing it makes every tender inside
`SLACK_ANNOUNCE_LOOKBACK_HOURS` eligible to be announced again under the new label — correct when
the destination channel really changes, wrong when a channel is only renamed.

**A clean sweep legitimately sends nothing.** `SLACK_MIN_SCORE` is 70 and no *real* ingested
notice has ever cleared it: the highest genuine score to date is 66, and every 70+ row in the
database is a `SEED-*` demo fixture. Do not read "no digest" as a broken notifier.

**Reliability properties, unchanged from the baseline and relied on here:**

* one `FetchRun` row per source per run, so one failing source cannot fail the run
* an overlapping window (`FETCH_MIN_LOOKBACK_HOURS=72`) so a late or missed run catches up
* upsert on `(source, source_notice_id)`; `first_seen_at` is immutable, which is what makes
  "new in this run" computable without touching the ingest path
* retry with backoff, `Retry-After` support, response-size and page caps per source

* a background sweep is held by a strong reference. `asyncio.create_task`'s result was once
  discarded, and the loop keeps only a weak one — a sweep sitting thirteen minutes inside a single
  `await` was collectable, and would vanish leaving its rows at `running` until the reaper closed
  them out an hour later.

**What the dashboard reports:** `GET /api/automation` returns the next run in Dhaka time, the last
run's outcome per source, Slack health, and the scheduler's *actual* registered jobs — so an
enabled-but-not-running scheduler is visible rather than silently promising a run that will never
fire. `SweepReport` then reports what a sweep you started actually found.

## 9. Frontend features

React 18 + TypeScript + Vite, plain CSS, and still only `react` + `react-dom` as *runtime*
dependencies — nothing else ships to the browser. `PRODUCT.md` owns product truth and `DESIGN.md`
owns the visual world; this is what they add up to.

* **One monochrome palette.** White ground, black type, black actions. Colour is reserved for
  status — green good, amber closing, red broken — and arrives as ink and hairline, never as a
  filled pill. There is no theme switch: `styles.css` carries a single `:root`.
* **A permanent left rail**, which replaced the masthead and the stat-tile row at once — the first
  tender used to begin 541 px down a 950 px viewport. It holds the lenses, the settings categories
  pinned to its bottom, and the two operator actions. One piece of chrome never moves, and that is
  what makes Settings findable at all.
* **Six lenses instead of tabs and tiles**: New this fetch, Open opportunities, Top scoring,
  Closing soon, Needs review, All tenders. A lens is a filter *preset*, not separate state, so
  `activeLens` reads the current filters back to decide which item is lit and a hand-edited filter
  set that matches no preset simply lights none. They overlap by construction — one tender is Open
  and Top scoring and Closing soon at once — so the counts do not sum to the total, which is why
  they render as muted badges rather than headline numerals. A count that can be clicked must
  equal the list it opens, so the score lenses filter on score *alone*: `/api/stats` counts purely
  on `relevance_score`, and adding a filter the count does not apply would put a number beside a
  lens that disagrees with itself.
* **Operator actions state their cost.** **Fetch** offers 3 / 7 / 30 / 90 days and prints the
  window it will search at the point of action — out of sight, that number is what let a sweep
  search a window the scheduler had already emptied and report success. **Re-score** reloads the
  profile and rewrites every row. Both surface the guard they hit (already sweeping, or the
  cooldown) rather than failing quietly.
* **A sweep reports what it found.** `SweepReport` names the window, keeps "seen and already
  stored" apart from "not returned at all" — only the second is a fault — and makes the
  new-notice count a button that filters the list to exactly those notices. Without it, a sweep
  that stored eight notices and one that stored none produced identical screens.
* **Settings slides out of the rail.** Filters keeps the results live beside it; Matching rules,
  Display, Automation, Sources and System take the width. It is not a right drawer and not an
  inline column — both were built and both were rejected. Shut, it goes `visibility: hidden`,
  because a panel merely translated off-screen keeps its tab stops.
* **Sources and rules are editable in place**: paste or rotate a key per source, probe and map a
  candidate feed before saving it, delete one you added; edit capability phrases as a paged table,
  add and remove capabilities, and preview what a rule change would move before it moves.
* **Result cards** show the estimated value and a deadline coloured at 14 days and 72 hours, clamp
  titles to two lines, mark anything first seen in the last run as **New**, and prefix the top
  relevance reason with an icon. Density is comfortable or compact; 25 results a page by default.
* **The detail panel explains the score**: three weighted subscore meters, the arithmetic written
  out (`0.55 × … + 0.30 × … + 0.15 × … = … → …`, and what capped it if anything did), reasons /
  disqualifiers / review flags kept apart, previous-next navigation (`j` / `k`), and a copy button
  on the raw payload. It never *recomputes* the score: the engine rounds half-to-even where
  JavaScript rounds half-up, and inferring a cap from the difference claimed one on 64 of 283
  notices.
* **Real states**: skeleton cards while loading, an empty state whose buttons actually clear the
  filters, and an error state that names the command to fix it.
* **The whole filter set lives in the URL**, so any view is shareable and survives a refresh —
  which is also how the Slack digest links to a pre-filtered dashboard, not just `?tender=<id>`.
  Those parameter names (`minimum_score`, `active_only`, `sort`, `tender`) are a contract with the
  digest.
* Keyboard navigable — the panel traps focus, Escape closes, focus returns to the opener. **Desktop
  only, confirmed**: a narrow window degrades rather than breaking, and there is no phone polish.
* **The landing view asks for score ≥ 70, open only**, both shown as removable chips. Know what
  that means before reading it as "nothing found": no *real* notice has ever scored 70, every 70+
  row is a `SEED-*` fixture, and the all-time real maximum is 66. Clear the floor to see what a
  sweep actually brought in — which is also why anything reporting on a sweep filters at score 0.

## 10. Troubleshooting

| Symptom | Fix |
| --- | --- |
| `Cannot reach the API. Is the backend running?` in the UI | Start the backend (`uvicorn app.main:app --port 8000`) or `docker compose up backend`. In dev the Vite proxy expects it on port 8000 — override with `VITE_PROXY_TARGET`. |
| SAM.gov card says *unavailable* | `SAM_GOV_API_KEY` is missing/invalid. Add it to `.env` and restart. Every other source keeps working. |
| CanadaBuys or AusTender run fails with HTTP 403 | Both sit behind a WAF that rejects unusual `User-Agent` strings. Keep the default `USER_AGENT` (`Mozilla/5.0 (compatible; tender-monitor/0.1)`). |
| PNCP run fails with `transport error: ReadTimeout` | PNCP is slow. It already uses a 60 s timeout and retries; reduce `PNCP_MAX_PAGES`/`PNCP_MODALIDADES` or raise `REQUEST_TIMEOUT_SECONDS`. A failure there never affects other sources. |
| A fetch returns 0 new tenders | Check the **window** before the connectors: a 72-hour sweep re-queries what the last scheduled run already emptied. Widen it (`{"days_back": 30}`), or run `python -m app.seed` for demo data. Beyond that it is normal — SDS/EHS software tenders are rare. |
| A sweep succeeded but the page looks empty | The landing view filters at score ≥ 70, which no real notice has ever reached. Clear the score chip, or read the sweep report, which counts at score 0. |
| `POST /api/fetch` returns `409` or `429` | A sweep is already in flight, or the 300 s operator cooldown has not elapsed (120 s for re-score). Both are deliberate — see D23. CI passes `X-Cron-Secret` to skip them. |
| A write returns `403` | `ALLOW_OPERATOR_ACTIONS=false`. That is the switch that closes the dashboard's writes; it must be set before the API is internet-reachable. |
| Slack links point at the wrong port | `PUBLIC_APP_URL` disagrees with `WEB_PORT`. Do not use a bare IP either — this host's address moved once already, which would have killed every link already sent. |
| Too many irrelevant rows stored | Keep `APPLY_KEYWORD_PREFILTER=true`, raise the dashboard minimum score, or tighten `PREFILTER_TERMS`. |
| `alembic upgrade head` says *Target database is not up to date* | Run it from `backend/`; the URL comes from `DATABASE_URL`. |
| Docker: `no such file or directory: backend/requirements.txt` | Build from the repository root (`docker compose up --build`); the backend image needs both `backend/` and `config/`. |
| Port already in use | 8000 (API), 8080 (Docker UI), 5173 (Vite). Set `API_PORT` / `WEB_PORT` in `.env` — and update `PUBLIC_APP_URL` to match — or pass `--port` in dev. |
| Scores changed after editing the YAML but the UI looks stale | Press **Re-score** (`POST /api/tenders/rescore`) — it clears the config cache and re-scores every stored notice. |

## 11. Layout

```
tender-monitor/
├── .github/workflows/
│   ├── scheduled-fetch.yml         00:00 / 12:00 Asia/Dhaka + manual replay
│   └── ci.yml                      tests, lint, both engines' migrations, actionlint
├── backend/
│   ├── app/
│   │   ├── api/routes.py           every REST endpoint
│   │   ├── connectors/             base + 8 built-ins + generic.py (a source described by
│   │   │                           data, not code) + registry + shared OCDS/keywords
│   │   ├── jobs/
│   │   │   ├── schedule.py         Dhaka -> UTC cron maths, derived not hardcoded
│   │   │   └── scheduled_fetch.py  the CLI entrypoint: window -> fetch -> score -> notify
│   │   ├── models/
│   │   │   ├── tender.py           Tender, FetchRun
│   │   │   ├── source.py           Source (a feed somebody added, with its mapping)
│   │   │   ├── app_setting.py      AppSetting (operator decisions + stored credentials)
│   │   │   └── notification.py     SlackNotification (the at-most-once ledger)
│   │   ├── services/
│   │   │   ├── relevance.py        scoring engine (frozen)
│   │   │   ├── ingest.py           upsert + run orchestration (additive only)
│   │   │   ├── matching_rules.py   the tunable part of the YAML, overridden in the database
│   │   │   ├── operator.py         single-flight + cooldown for the two expensive writes
│   │   │   ├── credentials.py      source keys and the Slack destination, write-only
│   │   │   ├── probe.py            try a candidate feed; refuses unsafe addresses
│   │   │   ├── notifier.py         Block Kit digests, claim -> post -> settle
│   │   │   ├── automation.py       read-only projection for /api/automation
│   │   │   ├── schedule_settings.py the stored sweep times and on/off decision
│   │   │   ├── scheduler.py        Asia/Dhaka CronTriggers, off by default
│   │   │   └── dhaka.py            UTC -> Dhaka rendering (presentation only)
│   │   ├── security.py             X-Cron-Secret recognition + hardening headers
│   │   ├── settings/config.py      pydantic-settings + secret redaction
│   │   ├── db.py  main.py  schemas.py  seed.py  logging_config.py
│   ├── alembic/versions/           6 revisions, head 9ad56685baa8; clean from empty on
│   │                               SQLite and Postgres
│   ├── tests/                      457 tests + saved API fixtures
│   └── requirements.txt  requirements-dev.txt  pyproject.toml  Dockerfile
├── frontend/
│   ├── src/
│   │   ├── components/             Sidebar (the permanent rail), SettingsPanel, Toolbar,
│   │   │                           TenderList, DetailPanel, SweepReport, SourcesPanel,
│   │   │                           SourceCard, MatchingRulesSettings, RunsTable,
│   │   │                           ScheduleEditor, TriggerSwitch, Pager, Notice, Icon
│   │   ├── components/settings/    SettingsPage + Sources / Automation / Display / System,
│   │   │                           AddSource (the field mapper), PhraseTable, SecretField
│   │   ├── state/                  urlFilters.ts (URL <-> filters), lenses.ts (the six
│   │   │                           presets), settingsNav.ts, preferences.ts
│   │   ├── pages/Dashboard.tsx     orchestration
│   │   ├── api/client.ts           the API client
│   │   └── labels.ts  styles.css  types/index.ts
│   └── package.json  vite.config.ts  nginx.conf  Dockerfile
├── config/relevance_profiles.yaml  all keywords, weights, patterns, caps (frozen, and never
│                                   rewritten — overrides live in app_settings)
├── docs/
│   ├── DECISIONS.md                24 records, D1–D24: every choice and accepted risk
│   ├── RUNBOOK.md                  deploy, rotate a secret, re-run a window, diagnose
│   └── DEMO.md                     repeatable demo with a fallback for every step
├── scripts/
│   ├── ci_summary.py               run report -> GitHub step summary
│   ├── fake_slack.py               offline webhook receiver, for testing without Slack
│   └── verify_workflow_locally.sh  replays the workflow's steps on this machine
├── docker-compose.yml              Postgres + API + nginx-served SPA
└── .env.example  README.md  CLAUDE.md  PRODUCT.md  DESIGN.md
```

## 12. Known gaps / next steps

* PNCP and (to a lesser degree) CanadaBuys are volume-capped; a full mirror needs incremental
  bookmarking per modalidade instead of a page cap.
* TED full-text search is stemmed by the API, so its result set is broader than the query
  suggests; precision comes from the relevance engine rather than the query.
* SAM.gov estimated values and TED lot-level values are only partially published; the model
  stores what the API returns.
* Attachments are linked, not downloaded or parsed. Document text is therefore not scored.
* Read endpoints are unauthenticated **by design** — the content is public procurement data
  already published by governments. The writes are not secret-gated either: they are
  cost-controlled instead (section 6, D23), which means the trust boundary is "anyone who can
  reach the dashboard can start a sweep, rotate a key or add a source". That is the same boundary
  D18/D19/D21 already accept for an accountless internal tool, and it is **only defensible while
  the API is not reachable from the internet**. Before that changes: set
  `ALLOW_OPERATOR_ACTIONS=false`, set `ENABLE_API_DOCS=false`, and read `docs/DECISIONS.md` D5,
  D18 and D23. Hardening headers are applied to every response; there is no rate limiting beyond
  the two cooldowns and no per-user auth.
* A source added from the dashboard makes the server fetch an operator-supplied URL. The probe
  refuses localhost, private ranges and the cloud metadata endpoint, but this is the one feature
  whose safety rests on that guard rather than on the network boundary.
* The repository is public so that Actions minutes are unlimited and free. The workflow files are
  therefore world-readable; they hold no secrets (every value arrives through `secrets.*` at run
  time), and GitHub does not expose secrets to `pull_request` runs from forks.
