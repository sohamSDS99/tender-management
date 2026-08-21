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

from fastapi import Depends, Header, HTTPException, Request, Response
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


def require_cron_secret(
    x_cron_secret: str | None = Header(default=None, alias=CRON_HEADER),
    settings: Settings = Depends(settings_dep),
) -> None:
    """Gate for operator/CI-only endpoints.

    Fails closed: with no CRON_SECRET configured the endpoint is refused
    outright rather than left open. Compared in constant time so the response
    latency cannot be used to guess the secret.
    """
    expected = settings.cron_secret
    if not expected:
        raise HTTPException(
            status_code=503,
            detail=(
                "This endpoint is disabled because CRON_SECRET is not configured. "
                "Set it in the environment and restart."
            ),
        )
    if not x_cron_secret or not secrets.compare_digest(x_cron_secret, expected):
        raise HTTPException(
            status_code=401,
            detail=f"Missing or invalid {CRON_HEADER} header.",
            headers={"WWW-Authenticate": CRON_HEADER},
        )
