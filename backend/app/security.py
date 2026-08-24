"""Protection for an API that has no user accounts.

README section 12 says plainly: no authentication, do not expose this as-is.
That stays true for *read* access, which is a documented decision (see
docs/DECISIONS.md D5) - the notices are public procurement data. What must never
be publicly callable is anything that writes or spends money on outbound
requests, so every write endpoint sits behind a shared secret and every response
carries hardening headers.
"""

from __future__ import annotations

import secrets

from fastapi import Depends, Header, Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from app.settings import Settings, get_settings

CRON_HEADER = "X-Cron-Secret"

# Applied to every response. 'none' everywhere is correct for a JSON API: it
# renders nothing, embeds nothing and must never be framed.
BASE_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-site",
    "Permissions-Policy": "geolocation=(), microphone=(), camera=()",
    "Content-Security-Policy": (
        "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
    ),
}
# Swagger UI and ReDoc pull their assets from jsdelivr, so the docs routes need a
# CSP that permits exactly that and nothing else. Set ENABLE_API_DOCS=false to
# remove the routes (and this exception) entirely.
DOCS_PATHS = ("/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect")
DOCS_CSP = (
    "default-src 'none'; img-src 'self' data: https://fastapi.tiangolo.com; "
    "script-src 'self' https://cdn.jsdelivr.net; style-src 'self' 'unsafe-inline' "
    "https://cdn.jsdelivr.net; font-src https://cdn.jsdelivr.net; connect-src 'self'; "
    "frame-ancestors 'none'; base-uri 'none'"
)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        response = await call_next(request)
        for key, value in BASE_HEADERS.items():
            response.headers.setdefault(key, value)
        if request.url.path in DOCS_PATHS:
            response.headers["Content-Security-Policy"] = DOCS_CSP
        return response


def settings_dep() -> Settings:
    return get_settings()


def has_cron_secret(
    x_cron_secret: str | None = Header(default=None, alias=CRON_HEADER),
    settings: Settings = Depends(settings_dep),
) -> bool:
    """True when the caller presented the shared secret. Never raises.

    This is the *trusted caller* test, not a gate: an operator in the dashboard is
    also allowed to run these actions, but under the cooldown and single-flight
    guards in app/services/operator.py instead. A trusted caller bypasses those,
    because CI and the scheduled entrypoint already control their own timing.
    See docs/DECISIONS.md (D23).

    Compared in constant time so response latency cannot be used to guess it.
    """
    expected = settings.cron_secret
    if not expected or not x_cron_secret:
        return False
    return secrets.compare_digest(x_cron_secret, expected)


# require_cron_secret() was removed in D23. No endpoint gated on it any more, and
# an unused gate sitting in a security module reads as protection that is not
# there. The remaining test of the secret is has_cron_secret() above, which grants
# a bypass of the operator guards rather than access.
