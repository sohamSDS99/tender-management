# Role

You are a senior platform engineer who has shipped ~30 production data-ingestion systems on strictly zero-budget infrastructure, and who has repeatedly had to make a live demo survive a non-technical executive audience. You are equally strong in Python/FastAPI/SQLAlchemy backends, React/TypeScript frontends, GitHub Actions automation, and free-tier hosting economics. You are pragmatic and adversarial about your own work: you assume any step you did not verify with a command is broken.

# Context

`tender-monitor/` is a **working, tested, single-tenant monitoring app** — not a greenfield project. My job for you is integration, automation, and deployment, not reinvention.

**What already exists and works**
- **Backend** — FastAPI + SQLAlchemy 2 + Alembic + APScheduler, Python 3.12, SQLite by default. 97 passing tests, no network needed.
- **Frontend** — React 18 + TypeScript + Vite, plain CSS. No router, no state library, only `react`/`react-dom`.
- **8 connectors over free public APIs/OCDS/CSV/RSS** — EU TED, US SAM.gov (free key, degrades gracefully), UK Find a Tender, UK Contracts Finder, World Bank, CanadaBuys, AusTender, Brazil PNCP. Retry/429/backoff, size caps, page caps, per-source `FetchRun` isolation.
- **Deterministic explainable relevance engine** — `final = 0.55·topic + 0.30·product_fit + 0.15·procurement_intent`, then hard caps and non-actionable multipliers. Every tender carries `relevance_score`, `relevance_category`, `fit_status`, `deployment_fit`, `relevance_reasons`, `disqualifiers`, `review_flags`, three subscores, `is_actionable`. No AI service, no paid API.
- **API** — `GET /health`, `GET /api/sources`, `GET /api/tenders` (full filter/sort/paginate set), `GET /api/tenders/{id}`, `POST /api/fetch` (202, queues one `FetchRun` per source), `GET /api/fetch-runs`, `POST /api/tenders/rescore`, `GET /api/stats`.
- **Data model** — `tenders` unique on `(source, source_notice_id)`, upserted via `content_hash`; `first_seen_at` immutable, `last_seen_at` per observation; all datetimes stored naive UTC. `fetch_runs` has `status`, per-source counters, `error_message`, `window_from/to`, and a `trigger` column already recording `manual`/`scheduled`.
- **Seed path** — `python -m app.seed --reset` loads 14 fixture tenders spanning every score band, deployment class and false-positive case, with zero external calls.
- **Deep link** — `frontend/src/pages/Dashboard.tsx:41` already reads `?tender=<id>` from the URL and opens that tender's detail panel. This is the hook Slack links must use.
- **Postgres is already supported with no code change** — `pip install "psycopg[binary]"` + `DATABASE_URL=postgresql+psycopg://…` (README §2).

**Known gaps that this build must close**
- `README.md` §12 states plainly: *"No authentication (by design) — do not expose the API publicly as-is."* This build puts it on the public internet.
- APScheduler is **in-process** — on any free host that sleeps on idle, an in-process timer is not a reliable trigger.
- Attachments are linked, never downloaded or parsed.

<frozen_core>
Do not change behaviour in these paths. Read them, call them, wrap them — never edit their logic:
- `backend/app/connectors/**` (all 8 connectors, `base.py`, `registry.py`, `keywords.py`, `ocds.py`)
- `backend/app/services/relevance.py`
- `backend/app/services/ingest.py` — additive only (new callers/params fine; scoring, upsert, hashing and windowing semantics unchanged)
- `config/relevance_profiles.yaml`
- The scoring/classification columns on `backend/app/models/tender.py`
Mechanical exceptions allowed: import moves, type hints, new additive columns via a new Alembic migration. If you believe a frozen file must change, stop that sub-task, write the reason into `docs/DECISIONS.md`, implement the closest non-invasive alternative, and list it in the final report.
</frozen_core>

<mockup_spec>
`tender-monitor-ui-mockup.html` (static HTML/CSS/JS, ~92 KB) is the **approved target design** for the React app, and it self-documents its deltas under its own "What changed against the current UI" section:
1. Header on a light surface, not a solid blue block; theme toggle added.
2. Stat tiles become filters; "Needs review" replaces the dead "failed connectors" tile; each tile gets a status dot and a one-line explanation.
3. A toolbar (search + sort + one "Filters & settings" button with a live count) replaces the filter sidebar; active filters render as individually removable chips.
4. All filters move into a right-hand settings drawer, grouped: Presets, Relevance, Fit & deployment, Capability, Sources, Country & status, Dates, Display. Checkbox lists with counts; true multi-select for country and status; score gets both min and max.
5. Display prefs (results per page, theme, card density) live in that same drawer.
6. Result cards show estimated value and deadline urgency (colour-coded at 14 days and 72 hours), titles clamped to 2 lines, a "New" marker for tenders first seen in the last run, icon-prefixed reasons.
7. Source health collapses to a one-line strip with a health pip per connector, expanding to per-source cards.
8. Detail panel explains the score visually — three weighted subscore meters plus the formula, grouped reasons/disqualifiers/flags, prev/next navigation, copy button on the raw payload.
9. Real loading (skeleton cards), empty (buttons that actually clear filters) and error (names the fix) states.
10. The whole filter set goes into the URL — shareable and refresh-surviving, not just `?tender=`.
The mockup's review-only scaffolding (the "What changed" panel, change pins, the `?tender=`-explainer copy) must NOT ship. The mockup still contains **"Fetch new tenders"** and per-source **"Fetch this source"** buttons — those are exactly what requirement 1 below deletes.
</mockup_spec>

<timezone_math>
Asia/Dhaka is UTC+6 year-round; Bangladesh observes no DST. Therefore:
- 00:00 Dhaka = **18:00 UTC the previous day** → cron `0 18 * * *`
- 12:00 Dhaka = **06:00 UTC the same day** → cron `0 6 * * *`
GitHub Actions cron is UTC-only and has no timezone field. Any APScheduler job must use `CronTrigger(hour=…, timezone="Asia/Dhaka")` so the intent is legible in code. Never hardcode a +6 offset arithmetic hack. Assert this mapping in a unit test.
</timezone_math>

<recommended_free_stack>
This is my recommended default because it is genuinely $0 with no credit card and — critically — it does not depend on the API host being awake at trigger time. Deviate only if you can justify it in `docs/DECISIONS.md`.
- **Trigger + compute: GitHub Actions scheduled workflow.** The ingest runs *inside the Actions runner* — checkout, install, execute a new CLI entrypoint, write directly to the shared Postgres, post to Slack. Free minutes are ample for 2 runs/day. This removes cold-start and sleep-on-idle from the critical path entirely.
- **Database: Neon free Postgres** (persistent, does not lose data on redeploy). SQLite on an ephemeral free host loses the database on every restart — that alone disqualifies the current default for production. Supabase free is an acceptable alternative but pauses on prolonged inactivity.
- **API: Render / Fly.io / Hugging Face Spaces free tier** (Docker). It may sleep; it is only needed when a human opens the dashboard. Add a warm-up ping in the workflow before any API-dependent step.
- **Frontend: Cloudflare Pages or Netlify free** — static Vite build, `VITE_API_BASE_URL` pointed at the API host.
- **Slack: Incoming Webhook** (free, no app review, no bot token needed).
- **Secrets: GitHub Actions repository secrets** + host env vars. Never committed.
Known free-tier risks you must document and mitigate: GitHub disables scheduled workflows after ~60 days of repo inactivity; Actions cron can be delayed under platform load (so the job must be window-based and idempotent, never "assume it ran exactly on the minute"); free API hosts cold-start (10–60 s).
</recommended_free_stack>

# Your task

Turn the existing `tender-monitor` repo plus the approved mockup into one deployed, fully automated, zero-cost, end-to-end system that fetches on a Dhaka 00:00/12:00 schedule with no human in the loop, announces new qualifying tenders to Slack with links back into our own dashboard, and survives a live COO demo without a single visible defect.

Deliver these five, in this order:

**1 — Automated triggers, and no manual fetching.**
Two triggers/day at 00:00 and 12:00 Asia/Dhaka. Authoritative trigger = the GitHub Actions scheduled workflow (`0 18 * * *` and `0 6 * * *` UTC), plus `workflow_dispatch` for demo replay. Add a CLI entrypoint (e.g. `python -m app.jobs.scheduled_fetch`) that performs window → fetch → score → notify in one process and exits non-zero on total failure. Convert the APScheduler job from `interval` to two `CronTrigger`s on `Asia/Dhaka` as a defence-in-depth fallback, default it **off** (`ENABLE_SCHEDULER=false`) in the deployed API so the same fetch cannot run twice, and record `trigger="cron"` on those `FetchRun` rows. Remove every manual-fetch affordance from the UI — the "Fetch new tenders" button and all "Fetch this source" buttons — and replace them with a read-only automation status widget: next scheduled run in Dhaka time, last run's outcome, per-source health. Keep `POST /api/fetch` on the server but gate it behind a shared-secret header (`CRON_SECRET`) so it is operator/CI-only, not a public button.

**2 — Slack notifications tied to our own tender pages.**
On every triggered run that yields qualifying tenders, post one digest message per run to a Slack channel via Incoming Webhook. Qualifying = **newly created in this run** (not re-observed updates) AND `relevance_score >= SLACK_MIN_SCORE` (default 70, the README's "highly relevant" band) AND `is_actionable = true`. Each entry links to **our system** as `{PUBLIC_APP_URL}/?tender={id}` — the `Dashboard.tsx` deep link — with the original notice URL as a secondary link. Show score, fit status, deployment fit, buyer, country, deadline (with urgency), estimated value, and the top relevance reason. Use Block Kit, cap the message at a sane number of items with an "+N more" link into a pre-filtered dashboard URL, and post a short "ran, nothing qualified" heartbeat when a run finds nothing (silence must never be ambiguous). Persist a `slack_notifications` table keyed to tender id + run so **a retried or double-fired run can never double-post** — prove this with a test. Failure to notify must not fail or roll back the ingest; it must surface as a visible degraded state.

**3 — Persistence, config and secrets for real deployment.**
Migrate to managed free Postgres, add the new Alembic migration(s) on top of `8a32d37f649c_initial_schema` for the notification table and any additive columns, keep SQLite working for local dev and tests, and confirm `alembic upgrade head` runs clean from empty on both engines. Add every new setting to `Settings` in `backend/app/settings/config.py` and to `.env.example` with comments: `SLACK_WEBHOOK_URL`, `SLACK_MIN_SCORE`, `SLACK_CHANNEL_LABEL`, `PUBLIC_APP_URL`, `CRON_SECRET`, `ENABLE_SLACK_NOTIFICATIONS`. Secrets are read from env only, never logged, never committed, and redacted in error paths exactly as the existing code redacts `api_key=***`.

**4 — Deploy all three tiers and prove they work together.**
Frontend (static host), API (Docker free tier, CORS updated to the real frontend origin, migrations on startup), database (managed Postgres), automation (Actions workflow with secrets configured). Add the minimum viable protection for a publicly reachable API given README §12 — at minimum: cron endpoint behind `CRON_SECRET`, no write endpoints publicly callable, security headers, and a documented explicit decision on read access. Then verify end to end against the live URLs, not localhost.

**5 — Port the mockup into the React app.**
Implement all 10 deltas in `<mockup_spec>`, minus the deleted fetch buttons and minus the review scaffolding, wired to the real API. Responsive to ~360 px, keyboard-navigable, both themes, accurate loading/empty/error states.

# Autonomy mandate

Build it, do not brief me on how to build it. **You** create the entire app structure and **you** create the cron job — end to end, on disk, working. Assume I will read your final report and nothing else.

**App structure — yours to create, in full.** Every new directory, package, `__init__.py`, module, service, schema, Alembic migration, Dockerfile change, `.env.example` entry, config file, test file and docs file is written by you, in place, complete. No skeletons for me to fill in, no `TODO: implement`, no "create a file called X with this content", no snippets in your report that only exist in your report. If the design needs a `backend/app/jobs/` package, a `backend/app/services/notifier.py`, a `slack_notifications` model, a migration on top of `8a32d37f649c_initial_schema`, a `docs/` tree, or a restructure of the frontend components to match the mockup — create all of it yourself and wire it together so the app runs.

**Cron job — yours to create, in full.** Write the actual `.github/workflows/scheduled-fetch.yml` (or equivalent) to disk yourself, complete and valid: both `schedule` cron expressions (`0 18 * * *`, `0 6 * * *`), `workflow_dispatch` for demo replay, Python setup, dependency install, the `DATABASE_URL` / `SLACK_WEBHOOK_URL` / `PUBLIC_APP_URL` / `CRON_SECRET` secret wiring by exact name, `alembic upgrade head`, the entrypoint invocation, a `timeout-minutes`, a `concurrency` group so two runs cannot overlap, and a failure path that is visible rather than silent. Also implement the APScheduler `CronTrigger` fallback in code. Do not describe a workflow for me to go and create.

**Do, don't instruct.** Prefer executing over explaining at every step: run the commands, create the files, run the migrations, run the test suite, run `ruff`, build the frontend, trigger the workflow, read its logs, read the Slack POST status code. Use the CLIs available to you (`gh`, provider CLIs, `curl`, `alembic`, `pytest`, `npm`) rather than handing me click-paths for things a command can do. Verify your own work — never ask me to check something you could check yourself.

**Where my account is genuinely required**, go right up to that boundary and stop cleanly: create the Neon project / Slack webhook / host service / repo secrets *if* you can authenticate, and if you cannot, still write every config file, generate every value you are able to generate (`CRON_SECRET` included), pin the exact secret names and the exact commands I will run — then put that single step in the end-of-run credential block. Never surface a manual step mid-build.

**Zero-handback target.** The number of actions I must perform before the demo works is a quality metric: minimize it, and enumerate every remaining one explicitly with its justification (account creation, OAuth consent, or a value only I possess). Any manual step that existed only because you chose to describe instead of do counts as a defect in `/harsh-review`.

# Build sequence

Work through these skills in order. Do not skip the gates.

1. `/goal` — first action. Convert this brief into the four-part contract (Goal / Context / Constraints / Done-means) with the acceptance list below as machine-checkable items. Plan before writing code.
2. `/backend-development` — for the jobs runner, notifier service, Postgres migration, settings, secret handling and API hardening.
3. `/systematic-debugging` — the moment anything fails (test, migration, deploy, webhook, cron). Diagnose from evidence before proposing a fix. No speculative patches, no "try this and see".
4. `/harsh-review` — when you believe you are done. Hostile senior-reviewer pass, scored out of 10 with evidence.
5. `/loop` — drive fix → re-verify → re-review until `/harsh-review` scores **≥ 9/10 with evidence**, max 5 iterations. If it plateaus below 9, stop and report exactly what is blocking it and why.

Use `/shipcheck` before the final report. Use any other skill where it clearly earns its place.

# Definition of done

Every line must be verifiable by a command or a URL, and the final report must show the actual evidence — command plus real output — not a claim. Anything you could not verify goes in an explicit "unverified" list.

1. `cd backend && pytest -q` — all 97 original tests still pass, plus new tests covering: the Dhaka↔UTC cron mapping, Slack payload construction, the newly-created-only + threshold + actionable filter, notification idempotency under a double-fired run, and the `CRON_SECRET` gate (401 without, 202 with).
2. `ruff check . && ruff format --check .` clean; `cd frontend && npm run lint && npm run build` clean.
3. `alembic upgrade head` runs clean from an empty database on **both** SQLite and Postgres.
4. A `workflow_dispatch` run of the Actions workflow completes green, writes `FetchRun` rows with `trigger="cron"`, and posts a real Slack message whose tender links open the correct tender in the deployed dashboard.
5. Re-running that same workflow immediately posts **no duplicate** tender entries.
6. The deployed frontend has **zero** manual-fetch controls, and shows next-run-in-Dhaka-time plus last-run outcome.
7. The cron schedule is proven correct: `0 18 * * *` and `0 6 * * *` UTC, asserted in a test as 00:00/12:00 Asia/Dhaka.
8. `curl https://<api>/health` returns healthy with the database check passing against Postgres.
9. All 10 mockup deltas implemented, verified at 360 px and desktop in both themes.
10. A repeatable demo script exists: `docs/DEMO.md` with exact steps, expected screen state at each step, a deterministic seed/replay path that produces a live Slack message on demand, and a documented fallback for every step that depends on the public internet.
11. `docs/RUNBOOK.md` covers deploy, rotate a secret, re-run a missed window, and diagnose a failed run. `docs/DECISIONS.md` records every architectural choice, every deviation from the recommended stack, and every accepted free-tier risk.
12. No secret anywhere in git history or committed files.
13. Every file the system needs exists on disk and is committed — including `.github/workflows/*.yml`, which must be present, valid (`actionlint` or `gh workflow list`) and visibly registered in the Actions tab. Your report contains **zero** "create this file" or "add this yourself" instructions.
14. The report ends with a numbered list of every remaining manual action required of me, each with the reason it could not be automated. A short list is the goal; an unjustified one is a defect.

# Rules

- **Do not stop mid-build to ask me questions.** This is one shot. Where the brief is ambiguous, choose the option that best serves a flawless COO demo, write the assumption into `docs/DECISIONS.md`, and keep going. Collect **all** credential needs into the single handoff block at the very end.
- **$0, hard constraint.** No paid tier, no trial credit that expires into a bill, no credit card required. If a component cannot be free, say so explicitly instead of quietly assuming spend.
- Respect `<frozen_core>`. The relevance engine's output for the 14 seed fixtures must be **byte-identical** before and after your work — prove it.
- Never invent tender data. Fixtures enter the database only via `python -m app.seed`. A demo that fakes a tender is a failed demo.
- Idempotency is not optional: a retried, delayed or double-fired run must never double-post to Slack or duplicate a tender row.
- One failing source must never fail a run, and a Slack failure must never lose ingested data — the existing per-source `FetchRun` isolation is the standard to match.
- Every datetime stays naive UTC in the database; Dhaka time is a presentation and scheduling concern only.
- Do not add a frontend dependency without justification in `docs/DECISIONS.md`. The app currently ships only `react` + `react-dom`.
- Report failures honestly with the real output. A green summary over a red test is the single worst outcome here — I am putting this in front of my COO on your word.
- No placeholder, TODO or "wire this up later" code paths in anything the demo touches.
- **Instruct nothing you could have done yourself.** Autonomous implementation of the full app structure and the cron job is a hard requirement, not a preference — see the autonomy mandate above.
- Never hand me a code block as a substitute for a file. If it belongs in the repo, write it to the repo.

# Before you start

Think first, in writing, before any edit:
1. State the 3–5 decisions that most determine whether this survives a live demo (trigger reliability, data persistence, duplicate suppression, public-API exposure, demo determinism) and commit to a position on each.
2. Name the highest-risk step and how you will verify it, not assume it.
3. Flag every point where the brief is genuinely ambiguous — including whether "the free website" means all 8 existing connectors or a narrower subset — state the assumption you are proceeding on, and continue.

Then build, gate, and finish with:
- A concise report: what shipped, evidence per acceptance item, what is unverified, what you deliberately did not do.
- **One consolidated credential request block**, listing for each secret: its exact env var name, where it is consumed, whether it is required or optional, the click-path to obtain it free, and the format to paste it back to you. Cover at minimum: `SLACK_WEBHOOK_URL`, the Postgres `DATABASE_URL`, `PUBLIC_APP_URL`, host/deploy tokens, `CRON_SECRET` (which you generate, not me), and the optional free `SAM_GOV_API_KEY`. Tell me plainly which are blocking a live demo and which the system degrades around.
