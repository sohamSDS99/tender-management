"""`--json` must put nothing but JSON on stdout.

The workflow redirects the entrypoint's stdout straight into report.json and
then parses it, so a single stray log line makes the run unreportable. This has
already broken once: logging was moved off alembic's stderr handler onto stdout,
which silently corrupted every --json report.
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]


@pytest.fixture
def sqlite_env(tmp_path, monkeypatch):
    env = {
        "DATABASE_URL": f"sqlite:///{tmp_path / 'cli.db'}",
        "PUBLIC_APP_URL": "http://localhost:8080",
        "SLACK_WEBHOOK_URL": "",
        "ENABLE_SCHEDULER": "false",
        "LOG_LEVEL": "INFO",
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
        "RELEVANCE_CONFIG_PATH": str(BACKEND.parent / "config" / "relevance_profiles.yaml"),
    }
    return env


def run_cli(env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "app.jobs.scheduled_fetch", *args],
        cwd=BACKEND,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )


def test_logging_goes_to_stderr_not_stdout() -> None:
    """The handler stream is the whole contract; assert it directly."""
    from app.logging_config import configure_logging

    configure_logging("INFO")
    handlers = logging.getLogger().handlers
    assert len(handlers) == 1
    assert getattr(handlers[0], "stream", None) is sys.stderr, (
        "application logs must not be written to stdout: the --json report is "
        "redirected from stdout into a file that CI parses"
    )


@pytest.mark.slow
def test_json_report_on_stdout_parses_cleanly(sqlite_env) -> None:
    result = run_cli(sqlite_env, "--seed", "--dry-run-notify", "--json")
    assert result.returncode == 0, result.stderr[-2000:]

    report = json.loads(result.stdout)  # the assertion: stdout is pure JSON
    assert report["trigger"] == "cron"
    assert report["seeded"] == 14
    assert report["notification"]["status"] == "dry_run"
    assert report["notification"]["payload"]["blocks"]

    # And the logs really did happen - just on the other stream.
    assert "scheduled run finished" in result.stderr


@pytest.mark.slow
def test_human_readable_mode_also_keeps_stdout_clean(sqlite_env) -> None:
    """Without --json, stdout is one summary line and nothing else."""
    result = run_cli(sqlite_env, "--seed", "--no-notify")
    assert result.returncode == 0, result.stderr[-2000:]
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    assert len(lines) == 1, f"expected one summary line on stdout, got {lines}"
    assert lines[0].startswith("batch ")
