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
> The hosted deployment on Railway: `docs/DEPLOY-RAILWAY.md`.
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

## 3. SAM.gov needs no API key

**Nothing to do here.** SAM.gov used to be the one source that needed a credential; it no longer
is. The default transport is the daily bulk extract, which is keyless, unmetered and richer than
the API. If you have inherited a `SAM_GOV_API_KEY`, you can delete it.

### Why the key went away

SAM meters its Get Opportunities API **per day, by account role**, and the free tier is far
smaller than it looks:

| Account | Requests/day |
| --- | --- |
| Non-federal, **no role** — what a personal key gets | **10** |
| Non-federal **with a role** (requires entity association) | 1 000 |
| Federal system account | 10 000 |

Ten a day is the whole budget, shared across every sweep. There is **no paid tier** — GSA grants
rate increases only to federal system accounts — so the only way up is registering an entity and
having its Entity Administrator approve a role, which for a non-US company means an NCAGE code,
then a UEI, then registration. Worth doing to **bid**; absurd as a prerequisite for reading.

Exceeding the quota returns `HTTP 429` with `code 900804 "Message throttled out"` and a
`Retry-After` pointing at the next `00:00 UTC`. This connector once spent up to 80 requests in a
single sweep — 20 pages plus 60 per-notice description fetches — so the first sweep of any day
exhausted the allowance and a perfectly valid key looked broken. In production SAM never returned
a single `200`.

### What replaced it

SAM publishes every active opportunity as one CSV, once a day, with no key, no login and no quota:

```
https://sam.gov/api/prod/fileextractservices/v1/api/download/
  Contract%20Opportunities/datagov/ContractOpportunitiesFullCSV.csv?privacy=Public
```

It is better than the API on every axis that matters here:

| | metered API | bulk extract |
| --- | --- | --- |
| Credential | free key, 10 requests/day | **none** |
| Cost of a sweep | 1 request, and 1 more per description | **1 download** |
| Description text | a second request per notice | **inline** |
| Notice types | `ptype=o,p,k,r` | filtered to the same four |
| Window | any past window | **only what is active now** |

The file was 242 MB in August 2026, gzipped in transit, and is streamed to a temporary file
before parsing — descriptions are free text and routinely contain newlines inside quoted fields,
so splitting the stream on newlines yields corrupt rows. `SAM_EXTRACT_MAX_BYTES` guards against
an unbounded stream. Like CanadaBuys and AusTender, it needs a browser-shaped `User-Agent`; the
default `USER_AGENT` satisfies it.

The API path is kept for the two things the extract cannot do — query a **past** window, and see
**closed** notices. `SAM_USE_BULK_EXTRACT=false` switches back to it, and then a key is required
again and `SAM_MAX_PAGES` / `SAM_MAX_DESCRIPTION_FETCHES` (1 and 0) keep it inside the quota.

Any key you do set is **write-only** over the API: `/api/sources` returns a `credential_hint`,
never a stored value. Nothing about reading a secret can be rate-limited, so that read path does
not exist — see section 6.

## 4. Sources and their limitations

| Source | Endpoint / feed | Auth | Pagination | Notes and limitations |
| --- | --- | --- | --- | --- |
| **EU TED** | `POST https://api.ted.europa.eu/v3/notices/search` | none | iteration token | Expert-search full-text query (`FT ~ "…"`) over `publication-date`. TED applies language stemming, so some hits are only loosely related — the relevance engine filters them. Stage is derived from the notice-type code (`pin*`→planning, `cn*`→tender, `can*`→award). |
| **US SAM.gov** | `ContractOpportunitiesFullCSV.csv` daily extract (API v2 optional) | **none** | whole file | The extract is every *currently active* opportunity — one keyless, unmetered download (242 MB, gzipped, streamed to disk before parsing) carrying the description **inline**. Filtered to the four notice types the API asked for with `ptype=o,p,k,r`, then to the sweep window on `PostedDate`. It cannot see closed notices or query a past window; `SAM_USE_BULK_EXTRACT=false` falls back to the metered API (10 requests/day on a role-less account — see section 3). Estimated values are published by neither. |
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

### 5.1 Marking a notice not relevant, and what the system learns (D27)

The score above is computed from phrases somebody wrote down. This is the other
half: patterns nobody wrote down, learned from what a reviewer actually rejected.

Mark any notice **Not relevant** — one click on the card, or the detail panel for
a mark with a note. That hides it, and once there are five rejections the system
starts hiding *new* notices matching the same patterns.

**How it learns.** For every word and adjacent word-pair it compares how often
the phrase appears in what you rejected against how often it appears in
everything else:

```
weight(phrase) = log P(phrase | rejected) − log P(phrase | everything else)
```

A phrase common everywhere — "contract", "services" — appears just as often in
both halves, so its weight lands near zero and it drops out on its own. There is
no stop-word list, no training job, no model file, and no new dependency: it is
`Counter` and `math.log` over the rows already stored, rebuilt whenever a verdict
changes.

**Four floors stop it over-reaching.** Under **5** rejections it predicts nothing
at all. A phrase must appear in **3** separate rejections to count. At least one
matched phrase must be strong on its own, so a notice is never hidden by a pile of
weak matches. And any phrase appearing in a notice you marked **Relevant** is
struck out of the model entirely — a phrase present in something you said yes to
can never hide anything.

**It cannot change a score.** A verdict decides only whether a notice is *shown*.
`relevance_score` stays exactly what the scoring engine computed.

**Nothing is discarded and every mark is reversible.** Hidden notices live in the
**Not relevant** lens with the reason each was hidden spelled out
(*"'laboratory furniture' appears in 6 notices you marked not relevant and only 1
other"*). Withdraw the mark and the patterns are re-derived from what is left.
`GET /api/feedback/learned` prints the whole model with its evidence, which the
Learned-patterns table under **Settings → Matching rules** renders.

Measured against the 611 notices stored on the development machine: twelve marks
hid 41 of them (7%), and none of the twelve notices scoring 40 or above — the
population a bidder actually reads — was hidden.


## 6. API

Interactive documentation: <http://localhost:8000/docs>.

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/health` | liveness + database check |
| GET | `/api/stats` | dashboard counters, distributions, filter option values |
| GET | `/api/tenders` | paginated, filtered, sorted list |
| GET | `/api/tenders/{id}` | full record incl. raw source payload |
| POST | `/api/tenders/rescore` | reload the profile and re-score every stored row |
| POST | `/api/tenders/{id}/feedback` | mark it relevant or not relevant, and re-learn from it, D27 |
| DELETE | `/api/tenders/{id}/feedback` | withdraw that mark, and unlearn what it taught |
| GET | `/api/feedback/learned` | the patterns learned from the marks, with the evidence for each |
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
| GET | `/api/auth/session` | who is signed in — `200` with `user: null` when nobody is |
| POST | `/api/auth/register` | create an account; first one on a fresh deployment becomes admin |
| POST | `/api/auth/login` · `/logout` | start or end a session (HttpOnly cookie) |
| GET · PATCH | `/api/auth/me` | your profile |
| POST | `/api/auth/me/password` | change it; ends every *other* session |
| GET · DELETE | `/api/auth/sessions` | your signed-in browsers; sign out everywhere else |
| GET · POST · DELETE | `/api/auth/invites` | invitations — **admin only** |
| GET · PATCH | `/api/auth/users` | roles and deactivation — **admin only** |
| GET · POST | `/api/auth/roster` | who may hold an account, each with their own access link — **admin only**; `role` is required on POST, D30 |
| PATCH · DELETE | `/api/auth/roster/{id}` | change the role a link grants (which withdraws the link, D30), or take the address off the list — **admin only** |
| POST · DELETE | `/api/auth/roster/{id}/link` | issue/replace or revoke that person's access link — **admin only** |
| POST | `/api/auth/invitation` | what an access link is — address, role, whether they have joined. Reads only; spends nothing, D30 |
| POST | `/api/auth/accept` | open an access link: creates the account if needed and signs in, no password, D29 |

**Every path above requires a session (D26)**, except `/health` and the six `/api/auth`
doors (`session`, `login`, `register`, `logout`, `invitation`, `accept`) — the last two are
public because their caller has no session by definition and the link is what stands in for
one. A signed-out caller gets `401`; a signed-in member who is not an administrator gets
`403` on the invite, user and roster endpoints — **only an administrator can change anybody's
role** (D30). `/docs`, `/redoc` and `/openapi.json` are gated too, and are removed entirely by
`ENABLE_API_DOCS=false`.

This reverses the older "reads stay open" position in D5 and D25 — see section 12 and D26.

`GET /api/tenders` query parameters: `query`, `sources`, `countries`, `categories`, `statuses`,
`fit_statuses`, `deployment_fits`, `minimum_score`, `maximum_score`, `published_from`,
`published_to`, `deadline_from`, `deadline_to`, `active_only`, `has_deadline`, `hidden`, `sort`
(`score_desc`, `score_asc`, `deadline_asc`, `deadline_desc`, `published_desc`, `published_asc`,
`first_seen_desc`), `page`, `page_size`. List parameters repeat: `?sources=ted&sources=sam`.

`hidden` is tri-state: `false` hides notices marked not relevant and those the learner
matched to them, `true` returns only those (this is the review screen an undo needs), and
omitting it ignores feedback entirely. The dashboard sends `false` by default.

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
| `POST`/`DELETE /api/tenders/{id}/feedback` | none | one row plus a local re-predict; marks are made in bursts while reading a list (D27) |
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
* `auto_irrelevant` / `auto_irrelevant_reasons` are the learner's conclusion, not a human's: derived, disposable and recomputed on every re-score. A reviewer's own verdict is in `tender_feedback`, never here

`first_seen_at` being immutable is load-bearing, not incidental: it is the only reason "new in
this run" is computable without touching the frozen ingest path, and it is what both the Slack
digest and the New lens filter on.

`fetch_runs` records `source`, `started_at`, `finished_at`, `status`
(`queued`/`running`/`success`/`partial`/`skipped`/`failed`), `records_received`,
`records_created`, `records_updated`, `records_skipped`, `error_message`, the window used, and a
`batch_id` grouping the per-source rows of one sweep.

Four smaller tables carry everything an operator changes:

| Table | Holds |
| --- | --- |
| `sources` | a feed somebody added: its URL, auth style, field mapping and enabled flag |
| `app_settings` | the sweep times, the on/off decision, the matching-rule overrides, and the stored credentials — each beating its environment variable and applying without a restart |
| `slack_notifications` | the at-most-once delivery ledger, unique on `(tender_id, channel_label)` |
| `tender_feedback` | one verdict per notice — `relevant` / `irrelevant`, with an optional note. Keyed on the tender, so re-marking is an update and no notice can hold two verdicts. Nothing in the fetch path writes here, which is what makes a verdict survive a re-score, a re-ingest and a content-hash change (D27) |

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
* **Seven lenses instead of tabs and tiles**: New this fetch, Open opportunities, Top scoring,
  Closing soon, Needs review, All tenders, Not relevant. A lens is a filter *preset*, not separate state, so
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
* **A notice can be marked not relevant, and the page says so in four places (D27).** One quiet
  button per card for the frequent act; both directions plus a note in the detail panel. Because
  hiding something is the one operation here that removes it from view, the removal is stated by a
  toolbar chip on the default view, by the **Not relevant** lens and its count in the rail, by a
  badge naming *which* hid it — a reviewer or the learner, which are different things — and by a
  sentence after each mark saying how many *other* notices it hid. A machine hide always prints
  its reason in words on the card, and the whole learned model with its evidence is a table under
  Matching rules. Nothing is discarded and every mark is reversible.
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
| SAM.gov card says *unavailable* | Only possible with `SAM_USE_BULK_EXTRACT=false`, which needs `SAM_GOV_API_KEY`. On the default extract transport no credential is involved, so this state cannot occur. |
| SAM.gov fails with `HTTP 429` | You are on the API path. The daily quota is spent, not the key wrong: a role-less account gets **10 requests/day**, resetting at `00:00 UTC`, and the body says `900804 "Message throttled out"`. The fix is the bulk extract (`SAM_USE_BULK_EXTRACT=true`, the default) — nothing you can pay for lifts the quota. See section 3. |
| SAM.gov fails with `bulk extract exceeded N bytes` | The extract outgrew `SAM_EXTRACT_MAX_BYTES`. It was 242 MB in August 2026; raise the guard. |
| SAM.gov extract fails with `HTTP 403` | The presigned S3 redirect rejects unusual `User-Agent` strings, exactly as CanadaBuys does. Keep the default `USER_AGENT`. |
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
│   └── package.json  vite.config.ts  nginx.conf.template  Dockerfile
├── config/relevance_profiles.yaml  all keywords, weights, patterns, caps (frozen, and never
│                                   rewritten — overrides live in app_settings)
├── docs/
│   ├── DECISIONS.md                28 records, D1–D28: every choice and accepted risk
│   ├── RUNBOOK.md                  deploy, rotate a secret, re-run a window, diagnose
│   ├── DEPLOY-RAILWAY.md           the hosted deployment: services, variables, trigger owner
│   └── DEMO.md                     repeatable demo with a fallback for every step
├── scripts/
│   ├── ci_summary.py               run report -> GitHub step summary
│   ├── fake_slack.py               offline webhook receiver, for testing without Slack
│   └── verify_workflow_locally.sh  replays the workflow's steps on this machine
├── docker-compose.yml              Postgres + API + nginx-served SPA
└── .env.example  README.md  CLAUDE.md  PRODUCT.md  DESIGN.md
```

## 12. Accounts

**Required.** Nothing on this dashboard is visible without one: signing out returns you to a
full-page sign-in screen, and the API answers `401` to anyone without a session
(`docs/DECISIONS.md` D26). This reverses D25, which shipped accounts that deliberately gated
nothing — the requirement changed, and the gate is enforced on the server rather than by
hiding pages, so `curl` gets the same `401` a browser does.

| Action | Where |
| --- | --- |
| Create the first account | The sign-in page offers **Create account** while no account exists. The first one becomes the administrator and needs no permission at all. |
| Add your team | **Settings → Account → Workspace members**: paste their addresses, choose **Member** or **Administrator**, then send each row's link. The role is required — it decides where the link lands them (D30). |
| Sign in | The sign-in page — it is the whole page when signed out, not a dialog over the dashboard. |
| Sign out | The account control at the foot of the left sidebar. |
| Profile, password, sessions | **Settings → Account**, or `/?settings=account`. |
| Invite someone | **Settings → Account → Invitations** (administrators only). |
| Change a role, deactivate someone | **Settings → Account → People** (administrators only — a member is refused by the API, not just by a hidden panel). |

**Register immediately after the first start.** Until somebody does, the next person to
reach the dashboard becomes the administrator. If you are too late, create one from a
shell on the host:

```bash
docker compose exec backend python -m app.accounts_cli create-admin \
  --email you@example.com --name "Your Name"
```

After that first account, **people join by opening their own link — there is no password**
(D29). Add their addresses under **Workspace members**, choose the role for that batch, and
each row comes back with a personal link. Send each person theirs, however you already talk
to them. The same link signs them in again later on any device, so there is nothing for them
to remember and nothing for you to reset.

**Where the link lands them depends on the role you chose (D30).** A **member** sees an
accept screen naming their address and the role they are joining as, presses one button, and
is in. An **administrator** is simply in — no button, because the people who hand out links
gain nothing from confirming one. Each row on the panel says which of the two it is.

**Change the role before you send the link.** Re-roling somebody who has not joined yet
withdraws their link on purpose: the same URL would otherwise land them somewhere different
from what you told them. The row will say so and offer **Generate link** — send the new one.
Once somebody has joined, their roster role is frozen history and their role is changed under
**People** instead.

**Only an administrator can change anybody's role.** That is enforced by the API on every
endpoint that writes one, not by hiding the panel: a member calling it directly gets `403`.
A member sees a short explanation under **Settings → Account** saying so, rather than an
empty space they cannot interpret.

**If you open somebody else's link while signed in as yourself**, the page says whose it is
and offers to leave it alone. It never swaps your session for theirs on its own.

**Every link is a live credential.** Whoever holds it is that person, so treat one like a
password: send it directly rather than to a channel, and press **Revoke** if it spreads.
Revoking does not end a session the person already holds — to cut somebody off entirely,
deactivate their account under **People**, which outranks any link.

A **single-use invitation** is still there for somebody not on the list at all, such as a
contractor. That path does still ask them to set a password.

Other things worth knowing:

* **Sign-in over HTTPS needs `SESSION_COOKIE_SECURE=true`; over plain HTTP it must stay
  `false`.** A Secure cookie is silently never sent over HTTP, and the symptom is a
  sign-in that succeeds and leaves you signed out.
* **`REQUIRE_SIGN_IN=false` reopens the API** exactly as it behaved before D26. It exists as
  the way back into a deployment whose gate is misbehaving, and it defaults to `true`.
* **`X-Cron-Secret` passes the gate**, because it is a machine identity that no browser holds
  (D5). With `CRON_SECRET` unset — the default — that door does not exist.
* **Most accounts have no password at all**, so there is nothing to reset — if somebody
  loses their link, issue them a new one. The bootstrap administrator does have one, and
  its recovery is `docker compose exec backend python -m app.accounts_cli reset-password
  --email …`, which also ends every session that account had.
* Changing a password signs out every *other* browser; this one stays in.
* The last remaining administrator cannot be demoted or deactivated, and nobody can
  deactivate themselves.
* Passwords, where they exist, are hashed with `hashlib.scrypt`, and session cookies are
  stored only as a SHA-256. Access links are stored readably — they have to be re-sendable
  — so a database dump does expose those; revoke and reissue if one ever leaks.

```bash
python -m app.accounts_cli list             # who has an account
python -m app.accounts_cli invite --email colleague@example.com
python -m app.accounts_cli reset-password --email you@example.com --reactivate
```

## 13. Known gaps / next steps

* PNCP and (to a lesser degree) CanadaBuys are volume-capped; a full mirror needs incremental
  bookmarking per modalidade instead of a page cap.
* TED full-text search is stemmed by the API, so its result set is broader than the query
  suggests; precision comes from the relevance engine rather than the query.
* SAM.gov estimated values and TED lot-level values are only partially published; the model
  stores what the API returns.
* Attachments are linked, not downloaded or parsed. Document text is therefore not scored.
* The API now requires a session (D26), but **that is not the same as being safe to expose**.
  Once inside, any account can start a sweep, rotate a source key or add a source — the controls
  there are costs, not permissions (section 6, D23), and the only role distinction is the small
  set of account-administration endpoints. Before putting this on the internet: set
  `ALLOW_OPERATOR_ACTIONS=false`, set `ENABLE_API_DOCS=false`, set `SESSION_COOKIE_SECURE=true`,
  and read `docs/DECISIONS.md` D5, D18, D23 and D26. Hardening headers are applied to every
  response; rate limiting is the two operator cooldowns and the per-account sign-in lockout,
  and nothing more.
* A source added from the dashboard makes the server fetch an operator-supplied URL. The probe
  refuses localhost, private ranges and the cloud metadata endpoint, but this is the one feature
  whose safety rests on that guard rather than on the network boundary.
* The repository is public so that Actions minutes are unlimited and free. The workflow files are
  therefore world-readable; they hold no secrets (every value arrives through `secrets.*` at run
  time), and GitHub does not expose secrets to `pull_request` runs from forks.
