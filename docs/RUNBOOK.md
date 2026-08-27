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

A full live sweep takes about **13 minutes** locally and about **21 minutes** on
a GitHub runner. That is normal - it is page-cap and network bound, not stuck.

Individual sources time out and retry from time to time; `pncp` is the usual
culprit (`ReadTimeout` on `pncp.gov.br`). One source failing never fails the run,
and the next sweep picks up what it missed because the window overlaps by at
least 72 hours.

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

## 4b. Configure or rotate the Slack bot token

The digest is delivered by `chat.postMessage` with a bot token. The alternative,
an incoming webhook, still works — but the token is preferred because it can be
revoked and reissued, whereas a webhook URL *is* its own credential.

**Current setup:** a private channel in the company workspace, under the display
name `Tender Monitor`. The workspace, token and channel ID are in `.env` and are
deliberately not repeated here — this repository is public. To see what is live:

```bash
grep -E '^SLACK_(CHANNEL_ID|CHANNEL_LABEL|BOT_USERNAME)=' .env
```

### Which transport is live

Derived from what is configured, and reported rather than assumed:

```bash
curl -s http://localhost:8081/api/automation | python3 -c \
  "import json,sys; s=json.load(sys.stdin)['slack']; print(s['transport'], s['status'], s['channel_label'])"
```

`bot_token`, `webhook` or `none`. A bot token wins when both are set.

### Required scopes

On the Slack app's **OAuth & Permissions** page:

| Scope | Why |
|---|---|
| `chat:write` | post at all |
| `chat:write.public` | post to a **public** channel without inviting the bot |
| `chat:write.customize` | honour `SLACK_BOT_USERNAME` / `SLACK_BOT_ICON_EMOJI` |
| `channels:read`, `groups:read` | look up a channel ID (setup only) |

For a **private** channel the bot must be invited — `chat:write.public` does not
reach private channels. The channel in use is private, so the bot was invited to
it; `chat:write.public` is configured anyway so a future move to a public channel
needs no scope change.

### Rotate the token

Do this whenever the token may have been exposed. It needs no code change.

1. Slack → the app → **OAuth & Permissions** → **Reinstall to Workspace**, which
   issues a new `xoxb-` token and invalidates the old one.
2. Put the new value in `.env` as `SLACK_BOT_TOKEN` (gitignored — never commit it).
3. `docker compose up -d backend` to pick it up.
4. Confirm the token before waiting for a sweep:

```bash
curl -s -X POST https://slack.com/api/auth.test \
  -H "Authorization: Bearer $SLACK_BOT_TOKEN"
```

`{"ok": true}` with the expected team name means the token is live. An `invalid_auth`
here is the whole diagnosis — no need to look at the app.

### Find a channel ID

An ID, not a name: a channel can be renamed, and a name lookup is the first thing
to break when it is.

```bash
curl -s "https://slack.com/api/conversations.list?types=public_channel,private_channel&exclude_archived=true&limit=200" \
  -H "Authorization: Bearer $SLACK_BOT_TOKEN" \
  | python3 -c "import json,sys; [print(c['id'], '#'+c['name'], 'member' if c.get('is_member') else '') for c in json.load(sys.stdin)['channels']]"
```

**Changing channel means changing two variables.** `SLACK_CHANNEL_ID` is where the
digest goes; `SLACK_CHANNEL_LABEL` is what the digest says *and* the ledger key
that makes an announcement at-most-once (D6). Change both together, and expect
every tender still inside `SLACK_ANNOUNCE_LOOKBACK_HOURS` (72h) to be announced
once more under the new label — the new channel has not seen them. Leaving the
label alone while moving the ID makes the ledger record a destination that is not
where the message went.

### Test without waiting for a sweep

Build the exact payload and print it without posting:

```bash
docker compose exec -T backend python -m app.jobs.scheduled_fetch \
  --seed --dry-run-notify --trigger manual --json
```

To post for real, drop `--dry-run-notify`. Note this writes to the ledger, so the
same tenders will not be announced again — use a throwaway database if you only
want to see the formatting:

```bash
docker compose exec -T -e DATABASE_URL=sqlite:////tmp/slack-test.db backend \
  python -m app.jobs.scheduled_fetch --seed --seed-reset --trigger manual --json
```

### Symptoms

| Error in the dashboard or logs | Cause |
|---|---|
| `invalid_auth` | token revoked, rotated, or copied wrong |
| `channel_not_found` | wrong `SLACK_CHANNEL_ID`, or a private channel the bot is not in |
| `not_in_channel` | public channel and `chat:write.public` is missing — invite the bot or add the scope |
| `missing_scope (needed=...)` | the error names the scope; add it and reinstall |
| `ratelimited` | throttled; retried automatically |
| status `unconfirmed` | the request left but Slack never confirmed. **Never retried** — a retry could post twice. Check the channel by eye (section 4, and D15) |

---

## 5. Slack links, and who can reach the dashboard

The dashboard is served to the whole company network with no login, by design -
see `docs/DECISIONS.md` D18. `PUBLIC_APP_URL` is the LAN address, so the deep
link in every Slack entry opens for colleagues and not only on the host:

```
PUBLIC_APP_URL=http://Sohams-MacBook-Air.local:8081
```

The hostname rather than the IP, and for a concrete reason: during development
the host's address moved from `192.168.1.5` to `192.168.0.133` between one
afternoon and the next. Every link sent before that would have been dead. The
mDNS hostname survived the change; it needs the client to resolve `.local`,
which Apple devices and Windows 10+ do but some corporate networks block. If
yours is one of them, ask IT for a fixed address instead.

The dashboard shows this value under **Sweep times and source health** and warns
when it is a bare IP or `localhost`, because a wrong value fails silently.

Colleagues open the address above directly, or just click a tender in
Slack. They can read everything and change nothing: both write endpoints return
`401` without the shared secret, and the database publishes no host port.

### If the links stop working

The LAN address is a DHCP lease and can be reassigned. Symptom: links that used
to open now time out.

```bash
ipconfig getifaddr en0        # the current address
```

If it changed, update `PUBLIC_APP_URL` in `.env` and recreate the API:

```bash
docker compose up -d backend
docker compose exec -T backend python -c \
  "from app.services.notifier import tender_permalink; print(tender_permalink(1))"
```

Digests already sent keep the old address; only new ones pick up the change.

Two durable fixes, better than editing this on every lease change:

1. **A DHCP reservation** for this machine on the router. Preferred - the address
   then never moves and every client resolves it with no extra protocol.
2. **The mDNS hostname**, verified working here:
   `PUBLIC_APP_URL=http://Sohams-MacBook-Air.local:8081`. Survives a lease
   change. Requires the client to resolve `.local`, which Apple devices and
   Windows 10+ do, but some corporate networks block.

### Working from an untrusted network

On a cafe or hotel network, "anyone on the network" stops meaning "colleagues".
Either stop the stack:

```bash
docker compose stop
```

or bind it to this machine only, by changing the published ports in
`docker-compose.yml` to `127.0.0.1:${WEB_PORT:-8080}:80` and
`127.0.0.1:${API_PORT:-8000}:8000`, then `docker compose up -d`.

### Before exposing it to the internet

Different decision entirely, and not covered by D18. At minimum: set
`ENABLE_API_DOCS=false`, put real authentication in front of the whole app, and
re-read `docs/DECISIONS.md` D5.

**D26 closed the dashboard**: every route now needs a session except `/health`
and the `/api/auth` doors, and a signed-out browser sees a sign-in page and
nothing else. That is a real boundary, not a hidden page — `curl` gets the same
401.

It is still not the same as being safe on the internet. Any account, once in, can
start a sweep, rotate a source key or add a source; the controls there are costs,
not permissions (D23). Before exposing this: `ALLOW_OPERATOR_ACTIONS=false`,
`ENABLE_API_DOCS=false`, `SESSION_COOKIE_SECURE=true`.

---

## 5b. Change the sweep times

Anyone using the dashboard can do this; it needs no secret and no restart.

1. Open **Sweep times and source health** at the bottom of the page (or click the
   "next in …" text in the header, which jumps straight to it).
2. Click the hours you want. Between 1 and 6 a day, in `SCHEDULER_TIMEZONE`
   (Asia/Dhaka). The UTC cron equivalents are shown as you pick, so
   "00:00 Dhaka = `0 18 * * *`" is visible rather than assumed.
3. **Save sweep times.**

The change is stored in the database and applied to the running scheduler
immediately. It survives a container restart, and from then on
`SCHEDULER_HOURS_LOCAL` in `.env` is ignored.

Same thing from the command line:

```bash
curl -X PUT http://localhost:8000/api/automation/schedule \
  -H 'Content-Type: application/json' -d '{"hours_local":[7,19]}'
```

Confirm it took effect — `scheduler_jobs` is read from the live scheduler, not
from config:

```bash
curl -s http://localhost:8000/api/automation | python3 -m json.tool
docker compose logs backend | grep -E "schedule changed|rescheduled"
```

To hand the schedule back to the environment default:

```bash
docker compose exec -T db psql -U tender -d tenders -c \
  "delete from app_settings where key = 'scheduler.run_hours_local';"
docker compose restart backend
```

Bad values are refused, not repaired: an empty list, an hour outside 0-23, or
more than six a day all return 422 with a message naming the problem, and the
previous schedule keeps running. See `docs/DECISIONS.md` D19 for why this
endpoint needs no shared secret when the others do.

**One caveat.** This does not rewrite `.github/workflows/scheduled-fetch.yml`,
whose cron is static YAML in git. It does not matter while the local scheduler
owns the schedule (D2), but if Actions ever becomes the trigger owner the two
would diverge — change the workflow file too, and run
`cd backend && ./.venv/bin/python -m pytest tests/test_jobs_schedule.py` which
checks the file against the code.

---

## 5c. Pause and resume the sweep

Use this when a source is rate-limiting the system, during a maintenance window,
or after a bad deploy. It needs no secret and no restart, and it takes effect on
the running process immediately.

1. Open **Sweep times and source health** at the bottom of the page.
2. **Automated sweeps** is the first control. Click **Pause sweeps**, then confirm
   with **Yes, pause sweeps** — pausing asks twice on purpose.
3. To restart: **Switch sweeps on**. One click, no confirmation.

While paused, three places say so, because a paused system otherwise looks like a
healthy one that has simply found nothing: a banner at the top of the dashboard,
` · sweeps paused` on the collapsed system summary, and the control itself showing
the time it was paused.

Same thing from the command line:

```bash
curl -X PUT http://localhost:8000/api/automation/trigger \
  -H 'Content-Type: application/json' -d '{"enabled":false}'
```

Confirm the scheduler actually stopped — `scheduler_jobs` is read from the live
scheduler, so an empty list is proof rather than a claim:

```bash
curl -s http://localhost:8000/api/automation | python3 -m json.tool
docker compose logs backend | grep -E "sweeps paused|sweeps resumed|scheduler started"
```

Pausing logs at **WARNING**, not INFO. The failure mode this guards against is a
pause nobody remembers making.

The decision is stored in the database and **overrides `ENABLE_SCHEDULER`**, so it
survives a container restart. To hand the decision back to the environment:

```bash
docker compose exec -T db psql -U tender -d tenders -c \
  "delete from app_settings where key = 'scheduler.enabled';"
docker compose restart backend
```

Pausing does not disturb the sweep times, so resuming restores whatever hours were
chosen. Notices published while paused are picked up on the next sweep only as far
back as `FETCH_LOOKBACK_DAYS` reaches — a long pause loses the notices that fall
outside that window, so use section 3 to re-run a missed window if it matters.

**One caveat**, the same one as section 5b: this does not touch
`.github/workflows/scheduled-fetch.yml`. If Actions owns the trigger against this
database, pausing here does not stop it — disable the workflow in GitHub instead.
And switching sweeps *on* here while Actions also runs makes two trigger owners
(D2): survivable, since ingest upserts and the Slack ledger de-duplicates, but it
wastes a full sweep's worth of requests. See `docs/DECISIONS.md` D21.

---

## 5d. Run a deeper sweep by hand

Use this when you want notices the schedule has not reached — after a pause, when
a source has been fixed, or when someone asks "is there anything we missed?".

1. On the dashboard, pick a depth next to the button: **3d / 7d / 30d / 90d**.
2. Press **Fetch last N days**.
3. The panel under the toolbar reports the sweep as it runs, then what it found,
   with a button that filters the list to exactly the new notices.

**Why the button's default is 30 days and not 3.** The two sweeps answer different
questions. The schedule looks back 72 hours (`FETCH_MIN_LOOKBACK_HOURS`) because
its job is to keep up with the present without missing a late amendment. A person
pressing the button is asking the opposite — look further than you do on your own.
They used to share the 72-hour window, so by the time anyone clicked, it held
nothing unseen: the sweep queried eight public services, stored nothing, and
reported success. Measured minutes apart on the same five connectors, 34 notices
came back over 72 hours and 119 over 30 days. See `docs/DECISIONS.md` D24.

Same thing from the command line — `days_back` is what matters, and the 72-hour
floor is enforced underneath whatever you pass:

```bash
curl -X POST http://localhost:8000/api/fetch \
  -H 'Content-Type: application/json' -d '{"days_back": 30}'

curl -s 'http://localhost:8081/api/fetch-runs?limit=8' | python3 -m json.tool
```

**Expect very few of them to score.** A deep sweep brings in volume, not
relevance: the engine is looking for SDS/EHS software procurement, which is rare.
A 30-day sweep on 2026-08-24 stored 46 new notices and the highest scored 35. That
is the system working — use the **New this fetch** tab, which filters at any
score, rather than the default view, whose floor of 70 no real notice has ever
reached.

**Change the default** with `OPERATOR_FETCH_DAYS_BACK` in `.env`, then
`docker compose up -d backend`. Raising it costs time, not correctness — every
write is an upsert on `(source, source_notice_id)`.

**One caveat.** `MAX_PAGES_PER_SOURCE` bounds each source, so "the last 90 days"
means "as much of it as 20 pages of results reach". A deep sweep is not a
guarantee of complete coverage for that period, and the run report does not say
when a source hit its cap.

### Do not restart the stack during a sweep

`docker compose up -d --build` recreates the backend, and `up -d frontend`
restarts it too because of `depends_on`. Either kills an in-flight sweep. Notices
already stored are safe — they are committed per notice as they arrive — but the
interrupted source records a `failed` run, and the sweep has to be re-run for it.
Check first:

```bash
docker compose exec -T db psql -U tender -d tenders -c \
  "select source, status from fetch_runs where finished_at is null;"
```

To rebuild only the web container without touching the API, use `--no-deps`:

```bash
docker compose up -d --build --no-deps frontend
```

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

The repository is **public**, so Actions minutes are unlimited and free — there is
no billing dependency and no monthly allowance to watch.

Both workflows have been run and are green. If a dispatch ever fails within a few
seconds with *"The job was not started..."*, that is an account-level block rather
than anything in this repository: check **Settings → Billing & plans**.

The workflow's step sequence can also be verified without GitHub, which is useful
when changing the workflow itself:

```bash
scripts/verify_workflow_locally.sh              # fixtures, seconds
MODE=live scripts/verify_workflow_locally.sh    # real connectors, ~13 min
```

Behaviour by configuration:

| `DATABASE_URL` secret | Event | What happens |
|---|---|---|
| absent | `schedule` | seed-fixture self-test, payload rendered into the step summary, nothing posted |
| absent | `workflow_dispatch` | honours `mode`; still renders rather than posts |
| present | any | live sweep against that database, real Slack post, idempotent |

Never enable both the Actions schedule against the live database **and**
`ENABLE_SCHEDULER=true` on the API — see `docs/DECISIONS.md` D2.

GitHub disables scheduled workflows after roughly 60 days without repository
activity. Any push resets the clock; check the Actions tab monthly.

Because the repository is public, treat the workflow files as readable by
anyone. They contain no secrets - every value arrives through `secrets.*` at run
time - and `pull_request` runs from forks are not given secrets by GitHub.

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

---

## 9. Accounts

**An account is now required to see anything (D26).** Everything in this section
is therefore also about access, not only about who has a profile.

### Create the first administrator

The first registration on a fresh deployment needs no invitation and becomes an
administrator. **Do it immediately after the first start** — until somebody
does, the next person to reach the dashboard takes that slot.

Open the dashboard, use the account control at the foot of the left sidebar, and
choose **Create account**. The dialog says out loud that this one becomes the
administrator.

If somebody beat you to it, or nobody did it and you would rather not race,
create one from a shell on the host instead:

```bash
docker compose exec backend python -m app.accounts_cli create-admin \
  --email you@example.com --name "Your Name"
```

The password is prompted for rather than passed as an argument, because an
argument is visible in `ps` and lands in shell history.

### Add your team (the ordinary way)

**Settings → Account → Workspace members.** Paste everyone's addresses — commas,
spaces or new lines all work — pick the role they should get, and add them.
Then **Join link → Create join link** and send that one link to all of them.

Each person opens it, enters their own address and a password, and is in. The
link only works for addresses on the list, so it is safe to post in a team
channel; anybody else who follows it is told to ask an administrator.

If the link spreads further than you meant, **Replace link**. That kills the old
one immediately. Anyone who has already joined is unaffected — the link grants
registration and nothing else.

Removing somebody from the list stops them *registering*. It does not close an
account they already have; for that, use **People → Deactivate**, which ends
their sessions too.

### Invite one outsider

For somebody with no company address — a contractor. **Settings → Account →
Invitations**, or from a shell:

```bash
docker compose exec backend python -m app.accounts_cli invite \
  --email colleague@example.com --role member
```

Either way you get a link. There is no mail transport in this product, so
**you deliver it** — Slack, email, however you already talk to that person.

Three things about the link, all deliberate:

* It is shown **once**. Only a SHA-256 of it is stored, so nothing can retrieve
  it later. If it is lost, withdraw the invitation and issue another.
* It is **single-use** and expires in `INVITE_LIFETIME_DAYS` (default 7).
* If you set an address on it, only that address can use it — which makes a
  forwarded link useless. Leave the address blank for "whoever takes the role".

### Somebody is locked out, or forgot their password

There is no self-serve reset, because there is no mailer. From a shell:

```bash
docker compose exec backend python -m app.accounts_cli reset-password \
  --email them@example.com
```

This also clears any failed-attempt lockout and ends every session that account
had. Add `--reactivate` if the account was deactivated.

A repeated-failure lockout clears itself after `LOGIN_LOCKOUT_MINUTES`
(default 15); it does not need a human.

### Somebody has left

**Settings → Account → People → Deactivate.** That ends their live sessions
immediately and refuses any further sign-in. It is reversible — the account and
its history stay — which is why it is offered instead of deletion.

Two refusals you will meet, both deliberate: you cannot deactivate your own
account, and you cannot demote or deactivate the last remaining administrator.
Promote somebody else first.

### Sign-in succeeds and leaves you signed out

Almost always `SESSION_COOKIE_SECURE=true` on a deployment served over plain
HTTP. A Secure cookie is silently never sent over HTTP, so the API's `200` is
honest and the next request is anonymous.

```bash
docker compose exec backend printenv SESSION_COOKIE_SECURE
```

It must be `false` for the plain-HTTP compose deployment and `true` only behind
TLS. Change it in `.env`, then `docker compose up -d` — a variable change needs
the container recreated, not just restarted.

If that is not it, check the browser is reaching the API on the *same origin* as
the dashboard. A cross-origin `VITE_API_BASE_URL` needs that origin listed in
`CORS_ORIGINS`, or the browser sends no cookie and reports nothing.

### Who has an account

```bash
docker compose exec backend python -m app.accounts_cli list
```

### Locked out of the whole deployment

Two different situations, and the second one is the emergency.

**You forgot your password, but the dashboard is fine.** Reset it from a shell —
see above. Nothing special about it.

**The gate itself is refusing everybody**, and since the gate also guards the
account endpoints, there is no way in through the UI. Reopen the API without a
deploy:

```bash
railway variables --service backend --set REQUIRE_SIGN_IN=false
```

That restores the pre-D26 behaviour — every read answers anybody — which is a
deliberate trade: an open dashboard on an internal network beats a dashboard
nobody can enter. Fix the cause, then set it back to `true` and confirm with:

```bash
curl -o /dev/null -w '%{http_code}\n' https://<host>/api/tenders    # want 401
curl -o /dev/null -w '%{http_code}\n' https://<host>/health         # want 200
```

The second line matters as much as the first: `/health` must stay public or
Railway's healthcheck fails and the next deploy rolls back, with the application
logs looking healthy the whole time.
