"""Structured (key=value) logging. Never logs credentials."""

from __future__ import annotations

import logging
import sys


class KeyValueFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        base = (
            f"ts={self.formatTime(record, '%Y-%m-%dT%H:%M:%S')} level={record.levelname} logger={record.name}"
        )
        extras = getattr(record, "context", None)
        msg = record.getMessage()
        line = f'{base} msg="{msg}"'
        if extras:
            line += " " + " ".join(f"{k}={v}" for k, v in extras.items())
        if record.exc_info:
            line += f' exc="{self.formatException(record.exc_info)}"'
        return line


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(KeyValueFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())
    logging.getLogger("apscheduler").setLevel("WARNING")


def log_ctx(logger: logging.Logger, level: int, msg: str, **context: object) -> None:
    logger.log(level, msg, extra={"context": context})
