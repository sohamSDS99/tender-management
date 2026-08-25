# Deploying on Railway

The hosted deployment of this app. Written down because almost none of it is
recoverable from the repository: Railway holds the build settings, the variables
and the start command, and two of the choices below are not the obvious ones.

Project **Tender-Management**, environment **production**. Three services.

| Service | Source | Build | Reachable at |
|---|---|---|---|
| `Postgres` | Railway PostgreSQL 16 template | managed | `postgres.railway.internal:5432`, private only |
| `backend` | this repo, `main` | `backend/Dockerfile` | `backend.railway.internal:8000`, private only |
| `frontend` | this repo, `main` | `frontend/Dockerfile` | the one public URL |

Only `frontend` has a public domain, which is the same shape as the
docker-compose layout: nginx serves the built SPA and proxies `/api` and
`/health` to the API, so the browser only ever talks to one origin and no CORS
is involved. Giving `backend` its own public domain would add a second
internet-facing entry point to an API that, per section 12 of the README, is not
built to be one - see the note on `ALLOW_OPERATOR_ACTIONS` below.

Both app services build from the repository root, because both Dockerfiles
expect that context (`backend/` needs `config/`, `frontend/` needs
`frontend/`). `dockerfilePath` selects which one. Watch patterns are set so a
frontend-only change does not rebuild the API and vice versa: `backend/**` plus
`config/**` for the API, `frontend/**` for the dashboard.

## The start command, and why it is not `--host ::`

    uvicorn app.main:app --host 0.0.0.0 --port 8000

Railway's own documentation pushes you toward binding `::` so the service is
reachable over the private network, which is IPv6. Do not do that here. With
`--host ::` the API comes up correctly and serves traffic, and the deployment
still never goes live: Railway's healthcheck probes over IPv4, every attempt
returns "service unavailable" for the full retry window, and the deploy ends in
`1/1 replicas never became healthy`. The logs are actively misleading, because
the application's own startup lines all look healthy.

`0.0.0.0` was measured to satisfy both paths - the IPv4 healthcheck passes, and
`frontend` still reaches `backend.railway.internal:8000` across the private
network. It is also what `backend/Dockerfile` already does by default, so the
start command here is really just making the requirement explicit.

## Variables

`DATABASE_URL` is assembled from references rather than copied, so rotating the
database password does not silently strand the API on a stale one:

    postgresql+psycopg://${{Postgres.PGUSER}}:${{Postgres.PGPASSWORD}}@${{Postgres.RAILWAY_PRIVATE_DOMAIN}}:${{Postgres.PGPORT}}/${{Postgres.PGDATABASE}}

The `+psycopg` driver prefix matters - Railway's own `DATABASE_URL` is a bare
`postgresql://` URL, which SQLAlchemy resolves to psycopg2, which is not
installed. `RUN_MIGRATIONS_ON_STARTUP=true` lets Alembic bring the schema up on
boot, which is how the initial six revisions were applied.

On `frontend`, `PORT` and `BACKEND_ORIGIN` are the two values
`nginx.conf.template` is rendered from;
`BACKEND_ORIGIN=http://backend.railway.internal:8000`.

### Two settings that deviate from the README

`ALLOW_OPERATOR_ACTIONS=true`, which the README and `.env.example` both say
should be false before the API is reachable from the internet. It is true here
because the dashboard's "Fetch new tenders" and "Re-score" buttons are the
product, and a deployment where they return 403 is not a working one. What
guards them is the cost control described in D23 - one sweep at a time, plus a
cooldown between operator-started runs - not authentication. Anyone who can
reach the dashboard URL can spend those sweeps. If that is not acceptable, set
this to false and drive sweeps with `X-Cron-Secret` instead.

`ENABLE_API_DOCS=false`, following the same section of the README. `/docs`,
`/redoc` and `/openapi.json` are off. Requesting `/docs` through the public URL
returns the dashboard, because nginx's SPA fallback catches anything that is not
`/api` or `/health`.

## Who owns the trigger

`ENABLE_SCHEDULER=true` on `backend`, `SCHEDULER_TIMEZONE=Asia/Dhaka`,
`SCHEDULER_HOURS_LOCAL=0,12`. The in-process APScheduler in this container is
the single trigger owner that D2 requires.

The `Scheduled fetch` GitHub Actions workflow still runs on its cron, but with
no `DATABASE_URL` secret configured on the repository it takes its ephemeral
path: it exercises the pipeline against a throwaway Postgres service container
and renders the Slack payload without posting. That is harmless.

**Adding a `DATABASE_URL` secret to the repository would break D2** - the
workflow would start writing to this same database on the same schedule as the
scheduler above, and every window would be fetched twice. If you ever want
Actions to own the trigger, set `ENABLE_SCHEDULER=false` here first.

## Not configured

`SAM_GOV_API_KEY` is unset, so seven of the eight sources run and SAM.gov
reports why it is off. `SLACK_WEBHOOK_URL` and `SLACK_BOT_TOKEN` are unset and
`ENABLE_SLACK_NOTIFICATIONS=false`, so tenders are ingested and scored but
nothing is announced. Both degrade by design; set them to turn the features on.

`CRON_SECRET` is set, so `POST /api/fetch` and `POST /api/tenders/rescore`
accept a trusted caller that bypasses the operator cooldowns.
