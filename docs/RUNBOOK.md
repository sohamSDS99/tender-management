# Runbook

Operating the deployed system. Every command here has been run on the host it
targets; nothing is theoretical.

Conventions used below:

- Repository root is the directory containing `backend/`, `frontend/`, `config/`.
- `WEB_PORT` defaults to `8080` but this machine uses **8081**, because an
  unrelated `nexus-traefik` container already holds 8080. Substitute your own.
- The dashboard and the API share one origin: nginx serves the built SPA and
  proxies `/api` and `/health` to the API container.

---

## 1. Deploy

```bash
cp .env.example .env      # then fill in SLACK_WEBHOOK_URL and CRON_SECRET
docker compose up -d --build
```

That starts three containers:

| Service | What it is | Where |
|---|---|---|
| `db` | PostgreSQL 16, data in the named volume `pgdata` | internal only |
| `backend` | FastAPI + APScheduler, migrations on startup | `http://localhost:8000` |
| `frontend` | nginx serving the built SPA, proxying the API | `http://localhost:8081` |

Verify, in this order:

```bash
docker compose ps
curl -s http://localhost:8081/health
curl -s http://localhost:8081/api/automation
docker compose logs backend | grep "scheduler started"
```

A healthy deployment answers:

- `/health` → `{"status":"ok","database":{"dialect":"postgresql","ok":true,...}}`
- `/api/automation` → `cron_utc: ["0 18 * * *","0 6 * * *"]`,
  `scheduler_running: true`, and two entries in `scheduler_jobs`
- the log line → `scheduler started timezone=Asia/Dhaka hours_local=0,12 next_run=...`

If `scheduler_running` is `false` while `scheduler_in_process` is `true`, the
dashboard says so in a red banner and **no run will fire**. Restart the API
container and re-check the log line.

### Stop, and keep the data

```bash
docker compose down            # volume survives
docker compose down -v         # deletes the database. There is no undo.
```

---

## 2. Rotate a secret

Secrets are read from the environment only. They are never written to a file the
app controls, never logged (`app/settings/config.py` `redact()` strips them from
any string before it is logged or stored), and `.env` is gitignored.

### Slack webhook

1. Create the replacement first: <https://api.slack.com/apps> → your app →
   **Incoming Webhooks** → **Add New Webhook to Workspace** → pick the channel.
2. Test it before committing to it, using the offline receiver so nothing is
   posted to a real channel by mistake:

   ```bash
   python scripts/fake_slack.py --port 9099          # terminal 1

   docker compose exec \
     -e SLACK_WEBHOOK_URL=http://host.docker.internal:9099/hook \
     -T backend python -m app.jobs.scheduled_fetch \
       --seed --seed-reset --trigger cron            # terminal 2
   ```

   Terminal 1 prints the exact Block Kit payload and answers `200 ok`.

   Two details that matter, because getting either wrong posts a real digest to
   the live channel instead:

   * the override must be passed with `docker compose exec -e`. A shell prefix
     (`SLACK_WEBHOOK_URL=... docker compose exec ...`) sets the variable on the
     *host*, not inside the container, so the container keeps using the real
     webhook from `.env`;
   * the host is `host.docker.internal`, not `localhost` — inside the container
     `localhost` is the container itself.
3. Put the real URL in `.env`, then `docker compose up -d backend`.
4. Revoke the old webhook in Slack.

Changing `SLACK_CHANNEL_LABEL` is **not** a rotation: it is the ledger key, so
every tender becomes eligible to be announced once more in the new channel.

### CRON_SECRET

```bash
python -c "import secrets;print(secrets.token_urlsafe(48))"
```

Put it in `.env`, `docker compose up -d backend`, and update the GitHub secret so
CI keeps matching:

```bash
gh secret set CRON_SECRET --body "<the new value>"
```

Confirm the gate still behaves — 401 without, 202 with:

```bash
curl -s -o /dev/null -w '%{http_code}\n' -X POST http://localhost:8000/api/fetch
curl -s -o /dev/null -w '%{http_code}\n' -X POST \
  -H "X-Cron-Secret: <the new value>" http://localhost:8000/api/fetch
```

An **empty** `CRON_SECRET` does not open the endpoint — it refuses with 503.

### Database password

Changing `POSTGRES_PASSWORD` in `.env` alone will **break the stack**. The
Postgres image only applies that variable when it initialises an empty data
directory, so an existing `pgdata` volume keeps the old password while the
backend starts using the new one, and every connection is refused.

Change it inside the database first, then in the environment:

```bash
# 1. change it in the running database
docker compose exec -T db psql -U tender -d tenders -c \
  "ALTER USER tender WITH PASSWORD 'the-new-password';"

# 2. put the same value in .env
#    POSTGRES_PASSWORD=the-new-password

# 3. recreate the backend so it picks up the new DSN
docker compose up -d backend
curl -s http://localhost:8081/health
```

Both services read the same `POSTGRES_PASSWORD`, so once step 1 is done they
cannot drift apart. If you have already changed `.env` and the backend is
refusing to connect, put the old value back, run step 1, then re-apply.

---

## 3. Re-run a missed window

The fetch window always overlaps (`FETCH_MIN_LOOKBACK_HOURS=72`, so at least
three days), and every write is idempotent: notices upsert on
`(source, source_notice_id)`, and a tender is announced at most once per channel
for all time. **Re-running is always safe.** It cannot duplicate a tender row and
it cannot re-post to Slack.

```bash
# The window the schedule would have used
docker compose exec -T backend python -m app.jobs.scheduled_fetch --trigger cron

# A wider window, e.g. after two days down
docker compose exec -T backend python -m app.jobs.scheduled_fetch \
  --trigger cron --days-back 7

# One source only
docker compose exec -T backend python -m app.jobs.scheduled_fetch \
  --trigger cron --sources ted find_a_tender

# Fetch without notifying anyone
docker compose exec -T backend python -m app.jobs.scheduled_fetch \
  --trigger cron --no-notify
```

Exit codes are meaningful:

| Code | Meaning | What to do |
|---|---|---|
| 0 | Ingest finished, notification settled | nothing |
| 1 | Total ingest failure — every source failed, or none ran | §4 |
| 2 | Ingested safely, **Slack delivery failed** | data is safe; the ledger marked those tenders retryable, so the next run re-announces them. Check the webhook. |

A full live sweep of all sources takes about **13 minutes**. That is normal —
it is page-cap and network bound, not stuck.

---

## 4. Diagnose a failed run

Work outward from the cheapest signal.

**1. What does the app say?**

```bash
curl -s http://localhost:8081/api/automation | python3 -m json.tool
```

`last_run` carries the batch id, worst-status-of-the-batch, per-source counts and
a redacted `errors` list. `slack.status` is `ok`, `degraded`, `unconfigured` or
`disabled`. The dashboard shows the same thing as a banner.

**2. Per-source detail.**

```bash
curl -s 'http://localhost:8081/api/fetch-runs?limit=20' | python3 -m json.tool
curl -s 'http://localhost:8081/api/fetch-runs?status=failed' | python3 -m json.tool
```

One failing source never fails the run — each gets its own `FetchRun` row. A
`skipped` status with `SAM_GOV_API_KEY not configured` is expected and harmless.

**3. Logs.**

```bash
docker compose logs backend --tail 200
docker compose logs backend | grep -E "scheduled run|slack|failed"
```

Logs go to **stderr**; stdout is reserved for the `--json` report, so a
redirected report stays machine-readable.

**4. The database directly.**

```bash
docker compose exec -T db psql -U tender -d tenders -c \
  "select source, status, trigger, records_received, records_created, error_message
     from fetch_runs order by started_at desc limit 10;"

docker compose exec -T db psql -U tender -d tenders -c \
  "select status, count(*) from slack_notifications group by status;"
```

### Symptom index

| Symptom | Likely cause | Fix |
|---|---|---|
| `/health` refuses to connect | API container down | `docker compose ps`, then `docker compose logs backend` |
| `/health` 500, dialect missing | database unreachable | `docker compose ps db`; the backend waits for a healthy `db` |
| Dashboard shows "Cannot reach the API" | nginx up, API down | as above; the banner names the exact command |
| `scheduler_running: false` but enabled | scheduler failed to start | restart `backend`, check for `scheduler started` |
| No Slack message, `slack.status: unconfigured` | `SLACK_WEBHOOK_URL` unset | set it in `.env`, recreate `backend` |
| `slack.status: degraded` | Slack answered and rejected the post | the detail field carries the redacted reason; tenders are safe and the next run retries |
| `slack.status: unconfirmed` | the POST left but no reply came back | see "Unconfirmed deliveries" below — this one needs a human |
| Digest arrived but links 404 | `PUBLIC_APP_URL` wrong, or reader is not on this machine | see §5 |
| One source always `failed` | upstream change or rate limit | check `error_message`; disable it with `ENABLE_<SOURCE>=false` if it is noisy |
| Every source `failed` | no outbound network from the container | check the host's connectivity |
| A tender was never announced | it scored below `SLACK_MIN_SCORE`, is not actionable, or was not new in that run | `select relevance_score, is_actionable from tenders where id=<id>;` and check `slack_notifications` |

### Unconfirmed deliveries

`slack.status: unconfirmed` means a digest left this system and Slack never
answered — a dropped connection or a read timeout after the request was sent. It
is genuinely unknown whether the message was rendered.

These are **deliberately not retried**. Slack's incoming webhooks have no
idempotency key, so re-sending a possibly-delivered digest would post it twice.
The system chooses "possibly missed, visibly flagged" over "silently duplicated",
and asks you to look:

```bash
curl -s http://localhost:8081/api/automation | python3 -c \
  "import json,sys; print(json.load(sys.stdin)['slack'])"

docker compose exec -T db psql -U tender -d tenders -c \
  "select tender_id, run_batch_id, claimed_at, response_code, error_message
     from slack_notifications where status = 'unconfirmed' order by claimed_at desc;"
```

Then check the channel for that timestamp:

* **The message is there** — nothing to do. Optionally mark it settled so the
  banner clears:

  ```bash
  docker compose exec -T db psql -U tender -d tenders -c \
    "update slack_notifications set status='sent', posted_at=now()
       where status='unconfirmed';"
  ```

* **The message is not there** — clear the rows so the next run announces them:

  ```bash
  docker compose exec -T db psql -U tender -d tenders -c \
    "delete from slack_notifications where status='unconfirmed';"
  docker compose exec -T backend python -m app.jobs.scheduled_fetch \
    --trigger cron --days-back 7
  ```

  Note the wider window: those tenders are no longer new, so the run needs a
  lookback that re-observes them.

### Force a re-announcement

Only do this deliberately — it exists so a failed delivery can be replayed.

```bash
docker compose exec -T db psql -U tender -d tenders -c \
  "delete from slack_notifications where tender_id = <id>;"
docker compose exec -T backend python -m app.jobs.scheduled_fetch \
  --trigger cron --days-back 7
```

---

## 5. Slack links do not open for a colleague

`PUBLIC_APP_URL` is baked into every digest entry as
`{PUBLIC_APP_URL}/?tender=<id>`. Its default, `http://localhost:8080`, only
resolves on the machine running the app.

- Same office network: set it to the host's LAN address, e.g.
  `http://192.168.1.42:8081`, and recreate `backend`.
- Remote: put a tunnel in front (`cloudflared tunnel --url http://localhost:8081`)
  and set `PUBLIC_APP_URL` to the tunnel URL.
- Before exposing the API beyond the machine, set `ENABLE_API_DOCS=false` and
  read `docs/DECISIONS.md` D5 — reads are deliberately unauthenticated.

Existing digests keep the old URL. Only new ones pick up the change.

---

## 6. GitHub Actions

Two workflows, both registered and visible in the Actions tab:

```bash
gh workflow list
gh run list --limit 10
gh run view <run-id>
gh workflow run scheduled-fetch.yml -f mode=replay   # deterministic demo
gh workflow run scheduled-fetch.yml -f mode=live -f days_back=7
```

**This account currently cannot start a runner.** Every dispatch fails in a few
seconds with *"The job was not started because recent account payments have
failed or your spending limit needs to be increased"*, and zero steps execute.
Private-repo Actions minutes bill against a paid allowance. Two ways out:

1. **Make the repository public** — Actions minutes are unlimited and free for
   public repositories. Permanently removes the billing dependency.
2. Resolve billing / raise the spending limit under
   **Settings → Billing & plans**.

Until then, the workflow's step sequence can still be verified on this machine,
which runs the same commands against a throwaway PostgreSQL container:

```bash
scripts/verify_workflow_locally.sh              # fixtures, seconds
MODE=live scripts/verify_workflow_locally.sh    # real connectors, ~13 min
```

Behaviour once runners work again, by configuration:

| `DATABASE_URL` secret | Event | What happens |
|---|---|---|
| absent | `schedule` | seed-fixture self-test, payload rendered into the step summary, nothing posted |
| absent | `workflow_dispatch` | honours `mode`; still renders rather than posts |
| present | any | live sweep against that database, real Slack post, idempotent |

Never enable both the Actions schedule against the live database **and**
`ENABLE_SCHEDULER=true` on the API — see `docs/DECISIONS.md` D2.

GitHub disables scheduled workflows after roughly 60 days without repository
activity. Any push resets the clock; check the Actions tab monthly.

---

## 7. Upgrade and rollback

```bash
git pull
docker compose up -d --build          # migrations run on startup
curl -s http://localhost:8081/health
```

**Take a dump before any rollback.** It is the only path that is guaranteed
lossless:

```bash
docker compose exec -T db pg_dump -U tender tenders > backup-$(date +%F).sql
```

To step back one revision:

```bash
docker compose exec -T backend alembic downgrade -1
docker compose exec -T backend alembic current
```

Three of the four revisions are cleanly reversible. The exception is
`935d4b1fc0ff (widen buyer country)`: it widened `buyer_country` from
`varchar(8)` to `varchar(64)` because the World Bank feed emits full country
names, so going back to a column that cannot hold `"Indonesia"` is lossy by
definition. Its `downgrade` shortens the values so the migration completes rather
than failing halfway, which re-introduces the defect the revision fixed — on
PostgreSQL those notices start being dropped again, silently, one row at a time.

If you need to roll back past that revision, restore the dump instead:

```bash
docker compose down
docker volume rm tender-monitor_pgdata
docker compose up -d db
cat backup-YYYY-MM-DD.sql | docker compose exec -T db psql -U tender -d tenders
```

## 8. Re-score after editing the relevance config

`config/relevance_profiles.yaml` is read at startup and cached. After editing it:

```bash
curl -s -X POST -H "X-Cron-Secret: $CRON_SECRET" \
  http://localhost:8000/api/tenders/rescore
```

This rewrites the scores on every stored tender. It does **not** notify anyone —
notification depends on a tender being newly created by a run, so a re-score can
never spam the channel. Note that `tests/test_relevance_baseline.py` pins the
engine's output for the 14 seed fixtures by hash; if you intend to change scoring,
regenerate it with `python -m tests.test_relevance_baseline`.
