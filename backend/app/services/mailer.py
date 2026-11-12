"""Send one email through Brevo's HTTP API (D43).

HTTP, not SMTP: Railway blocks outbound SMTP on its cheaper plans. ``httpx`` is
already a dependency, so this is one POST and nothing new to install.

``send`` never raises. Its callers are alarm checks that run inside a sweep, and
an email that cannot be sent must never become a sweep that cannot finish. It
answers True only when Brevo accepted the message; anything else - no key, a
refusal, a network error - is False, so the caller leaves the alarm unsent and
the next tick tries again.
"""

from __future__ import annotations

import logging

import httpx

from app.logging_config import log_ctx
from app.settings import Settings, redact

logger = logging.getLogger(__name__)

BREVO_URL = "https://api.brevo.com/v3/smtp/email"
TIMEOUT_SECONDS = 15


def send(subject: str, text: str, html: str, *, to: list[str], cc: list[str], settings: Settings) -> bool:
    """Send one email. Never raises. True only if Brevo accepted it (2xx)."""
    if not settings.brevo_api_key:
        log_ctx(
            logger,
            logging.WARNING,
            "email not sent (dry run: BREVO_API_KEY is empty)",
            subject=subject,
            to=",".join(to),
            cc=",".join(cc),
        )
        return False
    try:
        response = httpx.post(
            BREVO_URL,
            timeout=TIMEOUT_SECONDS,
            headers={"api-key": settings.brevo_api_key, "accept": "application/json"},
            json={
                "sender": {"email": settings.alarm_from, "name": "Tender Monitor"},
                "to": [{"email": a} for a in to],
                **({"cc": [{"email": a} for a in cc]} if cc else {}),
                "subject": subject,
                "textContent": text,
                "htmlContent": html,
            },
        )
    except Exception as exc:  # never raises: see the module docstring
        log_ctx(logger, logging.ERROR, "email failed", subject=subject, error=type(exc).__name__)
        return False
    if 200 <= response.status_code < 300:
        log_ctx(
            logger, logging.INFO, "email sent", subject=subject, to=",".join(to), status=response.status_code
        )
        return True
    log_ctx(
        logger,
        logging.ERROR,
        "email refused",
        subject=subject,
        status=response.status_code,
        body=redact(response.text[:300], settings),
    )
    return False
