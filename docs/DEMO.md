# Demo script

A repeatable, ten-minute walkthrough for a non-technical audience. Every step
names what should be on screen, and every step that touches the public internet
has a fallback that does not.

**Rehearse once end to end before the meeting.** The only step that can surprise
you is the live Slack post, and step 0 removes that risk.

Substitute your own port for `8081` if `WEB_PORT` differs (the default is `8080`;
this machine uses 8081 because another container holds 8080).

---

## Step 0 — Prepare (15 minutes before, not during)

```bash
cd tender-monitor
docker compose up -d --build
curl -s http://localhost:8081/health
curl -s http://localhost:8081/api/automation | python3 -m json.tool | head -20
```

Expect `"status":"ok"` with `"dialect":"postgresql"`, and
`"scheduler_running": true` with two entries in `scheduler_jobs`.

Then load the deterministic dataset and confirm Slack works, **before** anyone is
watching:

```bash
docker compose exec -T backend python -m app.jobs.scheduled_fetch \
  --seed --seed-reset --trigger cron
```

Expect `slack=sent` and `14 fixture tender(s) inserted`. Check the channel: one
digest, six tenders. That is your proof the webhook is alive.

Now open two browser tabs and leave them open:

1. `http://localhost:8081/` — the dashboard
2. the Slack channel

**If the Slack channel is not ready, or the network is unreliable:** run the
offline receiver instead and share your terminal for step 4. Nothing else in the
demo changes.

```bash
python scripts/fake_slack.py --port 9099
# then set SLACK_WEBHOOK_URL=http://host.docker.internal:9099/hook in .env
# and: docker compose up -d backend
```

---

## Step 1 — "It watches eight government sources on its own"

Point at the header.

**On screen:** *Next automated run — 22 Aug 2026, 00:00 (Dhaka)*, and
*Last run — success · N new*, with a green dot.

Say: it sweeps twice a day, midnight and midday Dhaka time. Nobody presses
anything. Then point at the health strip below the tiles:

**On screen:** *7 of 8 sources healthy · US SAM.gov unavailable · last sweep HH:MM*

Click it to expand. Eight cards, each naming its state. SAM.gov says exactly why
it is off: no API key. Say: a source failing never takes the run down — each one
is isolated, and the dashboard tells you which and why.

> **If asked "where's the refresh button?"** — there isn't one, deliberately.
> Fetching is automated, and the endpoint that starts a fetch needs a shared
> secret a browser is never given. That is the point.

---

## Step 2 — "It ranks them, and shows its reasoning"

Point at the tiles: *Tenders stored*, *Highly relevant*, *Closing ≤ 14 days*,
*Needs review*, *Connector problems*. Each is clickable.

Click **Highly relevant**.

**On screen:** the list narrows; a chip appears reading *Score ≥ 70*; the
*Filters & settings* button shows a count.

Say: the number in the green square is a relevance score, and it is not a guess.
Click the **top result**.

**On screen:** the detail panel opens with

- three meters — *Topic relevance*, *Product & deployment*, *Procurement intent*
- the arithmetic spelled out, e.g.
  `0.55 × 100 + 0.30 × 100 + 0.15 × 83 = 97.45 → 97. No caps or multipliers applied.`
- a list of plain-English reasons, each with a tick

Say: same inputs, same score, every time — and it will tell you exactly why.

Now the sharper half. Close the panel, open **Filters & settings**, drag
**Minimum** to 0, close the drawer, and search for `SDS`.

Find the notice scored around **20** — *Beschaffung eines
Gefahrstoffmanagement-Systems*. Open it.

**On screen:** a red *Disqualifiers* section: *Mandatory on-premises deployment:
'must be installed on customer servers'*, and the formula showing
`= 58.00 → 58, then capped or scaled to 20 by the disqualifiers below`.

Say: it also knows what to rule out. This one reads like a perfect match until
you notice the buyer requires on-premises installation, which we do not sell.
It caught that and capped the score.

---

## Step 3 — "Anything you're looking at is shareable"

With filters applied, point at the browser address bar.

**On screen:** the URL carries the filter set, e.g.
`?minimum_score=0&query=SDS&tender=8`.

Press **Copy link** in the detail panel, paste it in a new tab, hit enter.

**On screen:** the identical view, same tender open.

Say: no exports, no screenshots — send the link.

---

## Step 4 — "And it tells us without being asked"

This is the moment worth rehearsing. Run:

```bash
docker compose exec -T backend python -m app.jobs.scheduled_fetch \
  --seed --seed-reset --trigger cron
```

Switch to Slack.

**On screen:** one message, headed *6 new tenders scoring 70+*, then per tender:
title as a link, the score, fit status, deployment fit, buyer, country, deadline
with a colour dot, estimated value, and the top reason.

Click the **first tender's title**.

**On screen:** the dashboard opens with that exact tender's detail panel already
open.

Say: the link goes to our own system, not to the government site — so whoever
picks it up lands on our assessment first. The original notice is one click
further in.

### The idempotency beat — do not skip this

Run the **same command again**, without `--seed-reset`:

```bash
docker compose exec -T backend python -m app.jobs.scheduled_fetch --seed --trigger cron
```

**On screen:** Slack shows a short one-line message —
*Ran at HH:MM (Dhaka) · nothing scored 70+. No action needed.* — and **no repeat
of the six tenders**.

Say: it never posts the same tender twice, however many times it runs. And when
a run finds nothing, it still says so — silence is never ambiguous, so you always
know it is alive.

> **Why `--seed-reset` first, then without?** The reset deletes the fixture rows
> and their delivery records so the replay produces genuinely new tenders. The
> second run re-observes the same ones, which is exactly the case that must not
> re-post. The fixtures are committed data — no tender is ever invented.

---

## Step 5 — "It degrades honestly"

Optional, 60 seconds, and it lands well with an operations-minded audience.

```bash
docker compose stop backend
```

Reload the dashboard.

**On screen:** a red panel — *Cannot reach the API* — naming the exact command to
bring it back, not a spinner and not a blank page.

```bash
docker compose start backend
```

Reload. Everything returns.

---

## Fallbacks, by step

| Step | Depends on the internet | Fallback |
|---|---|---|
| 0 | Docker images, if not already pulled | pull them the day before; after that it is all local |
| 1 | nothing — reads the local database | — |
| 2 | nothing | — |
| 3 | nothing | — |
| 4 | Slack's API only | `scripts/fake_slack.py` and share the terminal; the payload shown is byte-identical to what Slack would render |
| 5 | nothing | — |
| any | dashboard unreachable | `curl -s http://localhost:8081/api/automation \| python3 -m json.tool` shows the same facts as JSON |

A **live** sweep of the real sources is deliberately not part of this script: it
takes about 13 minutes and depends on eight external services. If someone asks to
see real data arrive, show `docs/RUNBOOK.md` §3 and offer to run it afterwards.
The 320 tenders already in the database came from a real sweep.

---

## Questions you should expect

**"What does it cost to run?"** Nothing. Eight free public sources, a free Slack
webhook, and it runs on this machine. No paid API, no per-seat licence.

**"Could it miss a tender?"** Each run looks back at least 72 hours, so a delayed
or missed run catches up rather than skipping. Deduplication is on
`(source, notice id)`, so overlap costs nothing.

**"Could it spam the channel?"** No. A tender is announced at most once per
channel, for all time — enforced by a unique constraint in the database, not by
convention. Step 4 demonstrates it.

**"Who can reach it?"** Right now, only this machine. Nothing that writes is
callable without a shared secret. See `docs/DECISIONS.md` D5 for what would need
to change before exposing it.

**"What happens if Slack is down?"** The tenders are still ingested and stored;
the dashboard shows a red *Slack digest not delivered* banner, and the next run
re-announces them. Nothing is lost.

---

## Reset to a clean state

```bash
docker compose exec -T backend python -m app.jobs.scheduled_fetch \
  --seed --seed-reset --trigger cron --no-notify
```

Restores the 14 fixtures and clears their delivery records without posting
anything, so the demo can be run again immediately.
