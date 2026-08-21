#!/usr/bin/env python3
"""A stand-in Slack incoming webhook, for verifying the notifier without a real one.

    python scripts/fake_slack.py --port 9099
    SLACK_WEBHOOK_URL=http://localhost:9099/hook python -m app.jobs.scheduled_fetch --seed

Prints every received payload and answers exactly like Slack does ("ok", 200).
Used by docs/DEMO.md as the offline fallback when the real webhook is
unreachable, and by the runbook to test a rotated URL before committing to it.
"""

from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, HTTPServer

RECEIVED: list[dict] = []


class Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802  (http.server API)
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length).decode("utf-8")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b"invalid_payload")
            return
        RECEIVED.append(payload)
        print(f"\n=== payload {len(RECEIVED)} ({len(raw)} bytes) ===")
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        print(f"=== blocks: {len(payload.get('blocks', []))} ===", flush=True)
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *args: object) -> None:  # keep stdout to payloads only
        return


def main() -> None:
    parser = argparse.ArgumentParser(description="Fake Slack incoming webhook")
    parser.add_argument("--port", type=int, default=9099)
    args = parser.parse_args()
    print(f"fake slack webhook listening on http://localhost:{args.port}/hook", flush=True)
    HTTPServer(("127.0.0.1", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
