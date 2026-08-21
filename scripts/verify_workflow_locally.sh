#!/usr/bin/env bash
# Replay the scheduled-fetch workflow's steps on this machine.
#
# Useful in two situations:
#   * GitHub will not start a runner (billing/spending limit on a private repo),
#     so the workflow cannot be exercised in CI;
#   * you changed the workflow and want to know it works before pushing.
#
# It mirrors .github/workflows/scheduled-fetch.yml step for step against a
# throwaway PostgreSQL container, using the same commands and the same
# environment variables. It does not post to Slack unless SLACK_WEBHOOK_URL is
# set, exactly like the workflow's ephemeral mode.
#
#   scripts/verify_workflow_locally.sh                 # replay mode (fixtures)
#   MODE=live scripts/verify_workflow_locally.sh        # call the real connectors
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

MODE="${MODE:-replay}"
PG_CONTAINER="tm-workflow-verify"
PG_PORT="${PG_PORT:-55433}"
PYTHON="${PYTHON:-backend/.venv/bin/python}"
ALEMBIC="${ALEMBIC:-backend/.venv/bin/alembic}"

step() { printf '\n\033[1m=== %s ===\033[0m\n' "$1"; }
cleanup() { docker rm -f "$PG_CONTAINER" >/dev/null 2>&1 || true; }
trap cleanup EXIT

step "services: ephemeral PostgreSQL (mirrors the workflow's service container)"
cleanup
docker run -d --name "$PG_CONTAINER" \
  -e POSTGRES_USER=tender -e POSTGRES_PASSWORD=tender -e POSTGRES_DB=tenders \
  -p "${PG_PORT}:5432" postgres:16-alpine >/dev/null
for i in $(seq 1 30); do
  if docker exec "$PG_CONTAINER" pg_isready -U tender -d tenders >/dev/null 2>&1; then
    echo "postgres ready after ${i}s"; break
  fi
  sleep 1
done

export DATABASE_URL="postgresql+psycopg://tender:tender@localhost:${PG_PORT}/tenders"
export PUBLIC_APP_URL="${PUBLIC_APP_URL:-http://localhost:8080}"
export ENABLE_SCHEDULER=false
export RUN_MIGRATIONS_ON_STARTUP=false
export PYTHONUNBUFFERED=1

# The workflow renders the payload instead of posting whenever the database is
# ephemeral, because a throwaway ledger cannot honour at-most-once delivery.
NOTIFY_FLAG="--dry-run-notify"
# Note the ${ARR[@]+...} form: macOS ships bash 3.2, where expanding an empty
# array under `set -u` is an "unbound variable" error.
if [ "$MODE" = "replay" ]; then SOURCE_FLAG=(--seed --seed-reset); else SOURCE_FLAG=(); fi
expand_source_flag() { printf '%s\n' "${SOURCE_FLAG[@]+${SOURCE_FLAG[@]}}"; }

step "install: skipped (using backend/.venv)"
"$PYTHON" -c "import fastapi, sqlalchemy, alembic, psycopg; print('dependencies present')"

step "alembic upgrade head"
(cd backend && "../$ALEMBIC" upgrade head)
(cd backend && "../$ALEMBIC" current)

step "run the sweep (mode=$MODE)"
set +e
(cd backend && "../$PYTHON" -m app.jobs.scheduled_fetch \
    --trigger cron ${SOURCE_FLAG[@]+"${SOURCE_FLAG[@]}"} "$NOTIFY_FLAG" --json > report.json)
EXIT_CODE=$?
set -e
echo "entrypoint exit code: $EXIT_CODE"

step "summarise the run"
MODE_DB=ephemeral MODE_SLACK=dry-run RUN_EXIT_CODE="$EXIT_CODE" \
  GITHUB_EVENT_NAME=workflow_dispatch RUN_MODE="$MODE" \
  "$PYTHON" scripts/ci_summary.py backend/report.json

step "assert what the workflow asserts"
"$PYTHON" - <<'PY'
import json
report = json.load(open("backend/report.json"))
note = report["notification"]
assert report["exit_code"] == 0, report
assert report["trigger"] == "cron", report["trigger"]
assert note["status"] == "dry_run", note
assert note["payload"]["blocks"], "no Slack blocks rendered"
assert len(note["payload"]["blocks"]) <= 50, len(note["payload"]["blocks"])
print(f"ok: trigger={report['trigger']} candidates={note['candidates']} "
      f"blocks={len(note['payload']['blocks'])} exit={report['exit_code']}")
PY

step "FetchRun rows recorded with trigger='cron'"
docker exec "$PG_CONTAINER" psql -U tender -d tenders -c \
  "select trigger, status, count(*) as runs, sum(records_received) as received from fetch_runs group by trigger, status order by trigger;"
docker exec "$PG_CONTAINER" psql -U tender -d tenders -t -c \
  "select 'tenders stored: '||count(*) from tenders;"

step "PASSED — the workflow's step sequence works on this machine"
