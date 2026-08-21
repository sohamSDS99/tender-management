#!/usr/bin/env python3
"""Render a scheduled-fetch run report into a GitHub Actions step summary.

Kept out of the workflow YAML on purpose: building markdown inside nested shell
and Python quoting is how workflows acquire silent bugs. Reads the report written
by ``python -m app.jobs.scheduled_fetch --json`` and appends markdown to
``$GITHUB_STEP_SUMMARY`` (or stdout when run locally).

    python scripts/ci_summary.py backend/report.json
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

MAX_JSON = 8000
MAX_PAYLOAD = 12000
STATUS_ICON = {
    "success": "✅",
    "partial": "⚠️",
    "skipped": "⏭️",
    "failed": "❌",
    "queued": "⏳",
    "running": "⏳",
}
EXIT_MEANING = {
    "0": "✅ completed",
    "1": "❌ total ingest failure",
    "2": "❌ ingested safely, Slack delivery failed",
}


def env(name: str, default: str = "—") -> str:
    value = os.environ.get(name, "")
    return value if value else default


def render(report: dict | None) -> str:
    lines: list[str] = ["## Scheduled fetch", ""]
    exit_code = env("RUN_EXIT_CODE", "?")
    lines += [
        "| | |",
        "|---|---|",
        "| Schedule | 00:00 / 12:00 Asia/Dhaka (`0 18 * * *`, `0 6 * * *` UTC) |",
        f"| Database | `{env('MODE_DB')}` |",
        f"| Slack | `{env('MODE_SLACK')}` |",
        f"| Triggered by | `{env('GITHUB_EVENT_NAME')}` |",
        f"| Mode | `{env('RUN_MODE', 'live')}` |",
        f"| Exit code | `{exit_code}` — {EXIT_MEANING.get(exit_code, 'unknown')} |",
        "",
    ]

    if report is None:
        lines += ["> The run produced no report. See the step log above.", ""]
        return "\n".join(lines)

    totals = report.get("totals") or {}
    note = report.get("notification") or {}
    lines += [
        f"**Batch** `{report.get('batch_id')}` · trigger `{report.get('trigger')}`",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Sources run | {totals.get('sources', 0)} |",
        f"| Notices received | {totals.get('received', 0)} |",
        f"| New tenders | {totals.get('created', 0)} |",
        f"| Updated tenders | {totals.get('updated', 0)} |",
        f"| Seed fixtures inserted | {report.get('seeded', 0)} |",
        f"| Failed sources | {', '.join(totals.get('failed_sources') or []) or 'none'} |",
        f"| Slack | `{note.get('status')}` — {note.get('posted', 0)} posted, "
        f"{note.get('suppressed', 0)} already announced |",
        "",
    ]

    runs = report.get("runs") or []
    if runs:
        lines += ["### Per-source outcome", "", "| Source | Status | Received | New | Updated | Message |", "|---|---|---|---|---|---|"]
        for run in runs:
            icon = STATUS_ICON.get(run.get("status", ""), "")
            message = (run.get("error_message") or "").replace("|", "\\|")[:120]
            lines.append(
                f"| `{run.get('source')}` | {icon} {run.get('status')} | "
                f"{run.get('records_received', 0)} | {run.get('records_created', 0)} | "
                f"{run.get('records_updated', 0)} | {message} |"
            )
        lines.append("")

    trimmed = dict(report)
    trimmed["notification"] = {k: v for k, v in note.items() if k != "payload"}
    lines += ["<details><summary>Full run report</summary>", "", "```json", json.dumps(trimmed, indent=2)[:MAX_JSON], "```", "", "</details>", ""]

    payload = note.get("payload")
    if payload:
        heading = (
            "### Slack payload (dry run — not posted)"
            if note.get("status") == "dry_run"
            else "### Slack payload"
        )
        lines += [heading, "", "```json", json.dumps(payload, indent=2)[:MAX_PAYLOAD], "```", ""]
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    path = Path(argv[1]) if len(argv) > 1 else Path("report.json")
    report: dict | None = None
    if path.is_file():
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            report = None
    markdown = render(report)
    target = os.environ.get("GITHUB_STEP_SUMMARY")
    if target:
        with open(target, "a", encoding="utf-8") as fh:
            fh.write(markdown + "\n")
    else:
        print(markdown)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
