# Tender Monitor — SDS & EHS software opportunities

Finds public tenders that are worth bidding on for a **cloud-hosted SDS + EHS SaaS platform**
(SDS management, SDS authoring, SDS distribution, chemical/GHS compliance, EHS platform,
incident / inspection / audit management), and explains every score it gives.

* **Backend** — FastAPI + SQLAlchemy 2 + Alembic + APScheduler (Python 3.12, SQLite by default)
* **Frontend** — React + TypeScript + Vite, plain responsive CSS
* **Sources** — 8 modular connectors over free public APIs / OCDS / CSV / RSS feeds
* **Relevance** — deterministic, YAML-driven, fully explainable. No AI service, no paid API.

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
docker compose up --build
```

| What | URL |
| --- | --- |
| Frontend dashboard | <http://localhost:8080> |
| API | <http://localhost:8000> |
| Swagger UI | <http://localhost:8000/docs> |
| ReDoc | <http://localhost:8000/redoc> |
| Health | <http://localhost:8000/health> |

The SQLite database is stored in `./data/tenders.db` on the host, so data survives rebuilds.
Migrations run automatically at container startup.

Load demo data (14 fixture tenders covering every score band, deployment class and
false-positive case) without calling any external API:

```bash
docker compose exec backend python -m app.seed --reset
```

Then press **Fetch new tenders** in the UI, or:

```bash
curl -X POST http://localhost:8000/api/fetch \
  -H 'Content-Type: application/json' \
  -d '{"sources": ["ted", "find_a_tender", "contracts_finder"], "days_back": 3}'
```

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
pytest -q                    # 97 tests, no network access required
ruff check .                 # lint
ruff check . --fix           # autofix
ruff format .                # format

# frontend
cd frontend
npm run lint                 # tsc --noEmit
npm run build                # type-check + production build
npm run format               # prettier
npm run format:check
```

### Database migrations

```bash
cd backend
alembic upgrade head                                  # apply
alembic revision --autogenerate -m "describe change"  # create after editing app/models
alembic downgrade -1                                  # roll back one step
alembic current                                       # where am I
```

`app.db.init_db()` runs `alembic upgrade head` on startup (disable with
`RUN_MIGRATIONS_ON_STARTUP=false`). PostgreSQL needs no code change:

```bash
pip install "psycopg[binary]"
export DATABASE_URL=postgresql+psycopg://tender:tender@localhost:5432/tenders
alembic upgrade head
```

## 3. Getting the free SAM.gov API key

SAM.gov is the only source that needs a credential, and it is free:

1. Create an account at <https://sam.gov> (Entity registration is **not** required).
2. Sign in, open **Account Details → API Keys**, and request a *public* API key
   (<https://sam.gov/content/api-keys>).
3. Put it in `.env`: `SAM_GOV_API_KEY=xxxxxxxx`, then restart the backend.

Without the key, the connector reports `unavailable` in `/api/sources` and the source cards,
its runs are recorded with status `skipped`, and every other source keeps working.

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
| GET | `/api/sources` | every connector: enabled, key required, prefiltered, tender count, last run/status/error |
| GET | `/api/tenders` | paginated, filtered, sorted list |
| GET | `/api/tenders/{id}` | full record incl. raw source payload |
| POST | `/api/fetch` | start a fetch (returns immediately with run ids) — **requires `X-Cron-Secret`** |
| GET | `/api/fetch-runs` | run history with per-source counters and errors |
| POST | `/api/tenders/rescore` | reload `relevance_profiles.yaml` and re-score everything — **requires `X-Cron-Secret`** |
| GET | `/api/stats` | dashboard counters, distributions, filter option values |
| GET | `/api/automation` | next run in Dhaka time, last run's outcome, Slack health, the scheduler's actual registered jobs |
| PUT | `/api/automation/schedule` | set the times of day the sweep runs (1–6 local hours) — **no secret**, see D19 |
| PUT | `/api/automation/trigger` | switch automated sweeps on or off — **no secret**, see D21 |

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

# start a fetch (operator/CI only), then watch the runs
curl -X POST http://localhost:8000/api/fetch \
  -H 'Content-Type: application/json' -H "X-Cron-Secret: $CRON_SECRET" \
  -d '{"days_back": 3}'
curl "http://localhost:8000/api/fetch-runs?limit=8"
```

`POST /api/fetch` returns `202` immediately with one queued run per source, never waits for the
connectors, and refuses to start a source that is already running (returned in
`skipped_sources`). Progress and results are read from `/api/fetch-runs`.

Both write endpoints require the `X-Cron-Secret` header — `401` without it, `503` if the server
has no `CRON_SECRET` configured at all (it fails closed rather than staying open). Prefer the CLI
(`python -m app.jobs.scheduled_fetch`) over the endpoint: it does the whole run, including the
Slack digest, and exits with a meaningful code.

## 7. Data model

`tenders` is unique on `(source, source_notice_id)` and upserted, never duplicated:

* `content_hash` (sha256 over the meaningful fields) decides *created / updated / unchanged*
* `first_seen_at` never changes; `last_seen_at` is written on every observation
* all datetimes are stored in **UTC**; the original offset is kept in `source_timezone`
* `raw_payload`, `classification_codes`, `document_urls`, `relevance_reasons`,
  `disqualifiers`, `review_flags` are JSON columns
* attachment URLs are stored, files are not downloaded

`fetch_runs` records `source`, `started_at`, `finished_at`, `status`
(`queued`/`running`/`success`/`partial`/`skipped`/`failed`), `records_received`,
`records_created`, `records_updated`, `records_skipped`, `error_message` and the window used.

## 8. Automation, scheduling and reliability

Fetching is fully automated and there is **no way to start one from the UI**. What the UI *can*
do is decide when it runs, and whether it runs at all — see **Operator controls** below.

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

Pausing asks for confirmation, resuming does not, and a paused system says so in three places —
a banner at the top of the dashboard, the collapsed system summary, and the control itself with
the time it was paused. A paused system that looked healthy would be indistinguishable from one
that had simply found nothing. `docs/RUNBOOK.md` sections 5b and 5c are the operating procedures.

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

**Reliability properties, unchanged from the baseline and relied on here:**

* one `FetchRun` row per source per run, so one failing source cannot fail the run
* an overlapping window (`FETCH_MIN_LOOKBACK_HOURS=72`) so a late or missed run catches up
* upsert on `(source, source_notice_id)`; `first_seen_at` is immutable, which is what makes
  "new in this run" computable without touching the ingest path
* retry with backoff, `Retry-After` support, response-size and page caps per source

**What the dashboard reports instead of a fetch button:** `GET /api/automation` returns the next
run in Dhaka time, the last run's outcome per source, Slack health, and the scheduler's *actual*
registered jobs — so an enabled-but-not-running scheduler is visible rather than silently
promising a run that will never fire.

## 9. Frontend features

The dashboard is the approved mockup, wired to the real API. React 18 + TypeScript + Vite, plain
CSS, and still only `react` + `react-dom` as dependencies.

* **No manual fetching anywhere.** The header reports the automation instead: next run in Dhaka
  time, the last run's outcome with a status dot, and a per-connector health strip. A degraded
  state — a failed source, an undelivered digest, a scheduler that is enabled but not running —
  appears as a banner rather than being buried.
* **Stat tiles are filters**: tenders stored, highly relevant (70+, the Slack bar), closing within
  14 days, needs review, connector problems. Each has a status dot and a one-line explanation of
  what its number means.
* **Toolbar**: search, sort, and one "Filters & settings" button carrying a live count. Every
  active filter appears below it as an individually removable chip, so the result count is always
  explained.
* **Settings drawer** holds every filter, grouped — presets, relevance (min *and* max), fit &
  deployment, capability, sources, country & status, dates — with counts from `/api/stats`, plus
  the display preferences: results per page, card density, and theme (light / dark / system).
* **Result cards** show the estimated value and a deadline colour-coded at 14 days and 72 hours,
  clamp titles to two lines, mark anything first seen in the last run as **New**, and prefix the
  top relevance reason with an icon.
* **Detail panel explains the score**: three weighted subscore meters, the arithmetic written out
  (`0.55 × … + 0.30 × … + 0.15 × … = … → …`, and what capped it if anything did), reasons /
  disqualifiers / review flags kept apart, previous-next navigation (`j` / `k`), and a copy button
  on the raw payload.
* **Real states**: skeleton cards while loading, an empty state whose buttons actually clear the
  filters, and an error state that names the command to fix it.
* **The whole filter set lives in the URL**, so any view is shareable and survives a refresh —
  which is also how the Slack digest links to a pre-filtered dashboard, not just `?tender=<id>`.
* Keyboard navigable (drawers trap focus, Escape closes, focus returns to the opener), responsive
  to ~360 px, and correct in both themes.
* Default view: **open tenders scoring 50 or more** — the score floor and "open only" show as
  chips so the narrowing is never invisible. Clear them to audit what was rejected.

## 10. Troubleshooting

| Symptom | Fix |
| --- | --- |
| `Cannot reach the API. Is the backend running?` in the UI | Start the backend (`uvicorn app.main:app --port 8000`) or `docker compose up backend`. In dev the Vite proxy expects it on port 8000 — override with `VITE_PROXY_TARGET`. |
| SAM.gov card says *unavailable* | `SAM_GOV_API_KEY` is missing/invalid. Add it to `.env` and restart. Every other source keeps working. |
| CanadaBuys or AusTender run fails with HTTP 403 | Both sit behind a WAF that rejects unusual `User-Agent` strings. Keep the default `USER_AGENT` (`Mozilla/5.0 (compatible; tender-monitor/0.1)`). |
| PNCP run fails with `transport error: ReadTimeout` | PNCP is slow. It already uses a 60 s timeout and retries; reduce `PNCP_MAX_PAGES`/`PNCP_MODALIDADES` or raise `REQUEST_TIMEOUT_SECONDS`. A failure there never affects other sources. |
| A fetch returns 0 new tenders | Normal — SDS/EHS software tenders are rare. Widen the window (`{"days_back": 30}`), or run `python -m app.seed` for demo data. |
| Too many irrelevant rows stored | Keep `APPLY_KEYWORD_PREFILTER=true`, raise the dashboard minimum score, or tighten `PREFILTER_TERMS`. |
| `alembic upgrade head` says *Target database is not up to date* | Run it from `backend/`; the URL comes from `DATABASE_URL`. |
| Docker: `no such file or directory: backend/requirements.txt` | Build from the repository root (`docker compose up --build`); the backend image needs both `backend/` and `config/`. |
| Port already in use | 8000 (API), 8080 (Docker UI), 5173 (Vite). Change the mapping in `docker-compose.yml` or pass `--port`. |
| Scores changed after editing the YAML but the UI looks stale | Press **Re-score** (`POST /api/tenders/rescore`) — it clears the config cache and re-scores every stored notice. |

## 11. Layout

```
tender-monitor/
├── .github/workflows/
│   ├── scheduled-fetch.yml         00:00 / 12:00 Asia/Dhaka + manual replay
│   └── ci.yml                      tests, lint, both engines' migrations, actionlint
├── backend/
│   ├── app/
│   │   ├── api/routes.py           REST endpoints
│   │   ├── connectors/             base + 8 sources + registry + shared OCDS/keywords
│   │   ├── jobs/
│   │   │   ├── schedule.py         Dhaka -> UTC cron maths, derived not hardcoded
│   │   │   └── scheduled_fetch.py  the CLI entrypoint: window -> fetch -> score -> notify
│   │   ├── models/
│   │   │   ├── tender.py           Tender, FetchRun
│   │   │   └── notification.py     SlackNotification (the at-most-once ledger)
│   │   ├── services/
│   │   │   ├── relevance.py        scoring engine (frozen)
│   │   │   ├── ingest.py           upsert + run orchestration (frozen)
│   │   │   ├── notifier.py         Block Kit digests, claim -> post -> settle
│   │   │   ├── automation.py       read-only projection for /api/automation
│   │   │   ├── scheduler.py        two Asia/Dhaka CronTriggers, off by default
│   │   │   └── dhaka.py            UTC -> Dhaka rendering (presentation only)
│   │   ├── security.py             CRON_SECRET gate + hardening headers
│   │   ├── settings/config.py      pydantic-settings + secret redaction
│   │   ├── db.py  main.py  schemas.py  seed.py  logging_config.py
│   ├── alembic/versions/           4 revisions; clean from empty on SQLite and Postgres
│   ├── tests/                      198 tests + saved API fixtures
│   └── requirements.txt  requirements-dev.txt  pyproject.toml  Dockerfile
├── frontend/
│   ├── src/
│   │   ├── components/             TopBar, StatTiles, SourceStrip, Toolbar, TenderList,
│   │   │                           SettingsDrawer, DetailDrawer, Drawer, Pager, RunsTable,
│   │   │                           AutomationNote, Icon
│   │   ├── state/                  urlFilters.ts (URL <-> filters), preferences.ts (theme)
│   │   ├── pages/Dashboard.tsx     orchestration
│   │   ├── api/client.ts           read-only client
│   │   └── labels.ts  styles.css  types/index.ts
│   └── package.json  vite.config.ts  nginx.conf  Dockerfile
├── config/relevance_profiles.yaml  all keywords, weights, patterns, caps (frozen)
├── docs/
│   ├── DECISIONS.md                every architectural choice and accepted risk
│   ├── RUNBOOK.md                  deploy, rotate a secret, re-run a window, diagnose
│   └── DEMO.md                     repeatable demo with a fallback for every step
├── scripts/
│   ├── ci_summary.py               run report -> GitHub step summary
│   ├── fake_slack.py               offline webhook receiver, for testing without Slack
│   └── verify_workflow_locally.sh  replays the workflow's steps on this machine
├── docker-compose.yml              Postgres + API + nginx-served SPA
└── .env.example  README.md
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
  already published by governments. Every endpoint that writes, or spends an outbound request
  (`POST /api/fetch`, `POST /api/tenders/rescore`), requires the `X-Cron-Secret` header and
  **fails closed** with 503 when `CRON_SECRET` is unset. Hardening headers are applied to every
  response. There is no rate limiting and no per-user auth: set `ENABLE_API_DOCS=false` and read
  `docs/DECISIONS.md` D5 before making the API reachable beyond the machine it runs on.
* The repository is public so that Actions minutes are unlimited and free. The workflow files are
  therefore world-readable; they hold no secrets (every value arrives through `secrets.*` at run
  time), and GitHub does not expose secrets to `pull_request` runs from forks.
