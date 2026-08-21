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
| POST | `/api/fetch` | start a fetch (returns immediately with run ids) |
| GET | `/api/fetch-runs` | run history with per-source counters and errors |
| POST | `/api/tenders/rescore` | reload `relevance_profiles.yaml` and re-score everything |
| GET | `/api/stats` | dashboard counters, distributions, filter option values |

`GET /api/tenders` query parameters: `query`, `sources`, `countries`, `categories`, `statuses`,
`fit_statuses`, `deployment_fits`, `minimum_score`, `maximum_score`, `published_from`,
`published_to`, `deadline_from`, `deadline_to`, `active_only`, `has_deadline`, `sort`
(`score_desc`, `score_asc`, `deadline_asc`, `deadline_desc`, `published_desc`, `published_asc`,
`first_seen_desc`), `page`, `page_size`. List parameters repeat: `?sources=ted&sources=sam`.

```bash
# highly relevant, cloud-compatible, still open
curl "http://localhost:8000/api/tenders?minimum_score=70&active_only=true\
&deployment_fits=cloud_required&deployment_fits=cloud_preferred&sort=deadline_asc"

# start a fetch, then watch the runs
curl -X POST http://localhost:8000/api/fetch -H 'Content-Type: application/json' -d '{"days_back": 3}'
curl "http://localhost:8000/api/fetch-runs?limit=8"
```

`POST /api/fetch` returns `202` immediately with one queued run per source, never waits for the
connectors, and refuses to start a source that is already running (returned in
`skipped_sources`). Progress and results are read from `/api/fetch-runs`.

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

## 8. Scheduling and reliability

* APScheduler fetches every enabled source every `FETCH_INTERVAL_HOURS` (default 6) with a
  single job id and `max_instances=1`, so `uvicorn --reload` cannot duplicate it. Disable with
  `ENABLE_SCHEDULER=false`.
* The window is always at least `FETCH_MIN_LOOKBACK_HOURS` (default 72) so amendments and late
  updates are re-observed even with `FETCH_LOOKBACK_DAYS=1`.
* One failing source never fails the run: each source has its own `FetchRun` row and its own
  error message; a malformed individual record is skipped without dropping the page (counted in
  `records_skipped`, run status becomes `partial`).
* Only temporary failures are retried (408/425/429/5xx and transport errors) with exponential
  backoff; HTTP 429 honours `Retry-After` (seconds or HTTP date). 4xx fails fast.
* Content types are validated, response size is capped (`MAX_RESPONSE_BYTES`), pages per source
  are capped (`MAX_PAGES_PER_SOURCE`).
* Structured `key=value` logs. API keys are never logged: URLs are redacted
  (`api_key=***`) in errors and log lines.

## 9. Frontend features

* Fetch button with live polling, last successful fetch time, re-score button
* Summary cards: tenders stored, highly relevant (70+), closing within 14 days, failed connectors
* Source health cards (status, counts, last run, error, notes, key requirement)
* Filters: search, minimum score, fit status, deployment fit, capability, source, country,
  status, deadline, "open opportunities only", sort, page size
* Colour bands — green = cloud-compatible good/excellent fit, amber = possible/manual review,
  red = disqualified (mandatory on-premises, offline, false positive)
* Detail panel: full description, all reasons/disqualifiers/flags, subscores, classification
  codes, value, dates, documents, original notice link, collapsible raw source metadata
* Loading, empty and error states; responsive down to ~360 px; open tender is shareable
  via `?tender=<id>`
* Default view: **active tenders scoring 50 or more** — clear the filters to audit rejected ones

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
├── backend/
│   ├── app/
│   │   ├── api/routes.py           REST endpoints
│   │   ├── connectors/             base + 8 sources + registry + shared OCDS/keywords
│   │   ├── models/tender.py        Tender, FetchRun
│   │   ├── services/               relevance.py, ingest.py, scheduler.py
│   │   ├── settings/config.py      pydantic-settings
│   │   ├── db.py  main.py  schemas.py  seed.py  logging_config.py
│   ├── alembic/                    migrations
│   ├── tests/                      97 tests + saved API fixtures
│   ├── requirements.txt  requirements-dev.txt  pyproject.toml  Dockerfile
├── frontend/
│   ├── src/{api,components,pages,types}/   client, dashboard, panel, filters
│   ├── package.json  vite.config.ts  nginx.conf  Dockerfile
├── config/relevance_profiles.yaml  all keywords, weights, patterns, caps
├── docker-compose.yml  .env.example  README.md
```

## 12. Known gaps / next steps

* PNCP and (to a lesser degree) CanadaBuys are volume-capped; a full mirror needs incremental
  bookmarking per modalidade instead of a page cap.
* TED full-text search is stemmed by the API, so its result set is broader than the query
  suggests; precision comes from the relevance engine rather than the query.
* SAM.gov estimated values and TED lot-level values are only partially published; the model
  stores what the API returns.
* Attachments are linked, not downloaded or parsed. Document text is therefore not scored.
* No authentication (by design) — do not expose the API publicly as-is.
