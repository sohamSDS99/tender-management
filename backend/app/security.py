"""Response hardening, the shared secret, and who the caller is.

README section 13 says plainly: no authentication, do not expose this as-is.
That stays true for *read* access, which is a documented decision (see
docs/DECISIONS.md D5) - the notices are public procurement data. What must never
be publicly callable is anything that writes or spends money on outbound
requests, so those endpoints carry cost limits (D23) and every response carries
hardening headers.

Since D25 the API also knows who is asking, when they have signed in. That is a
separate axis from everything above and it is important not to confuse them:
**identity here grants nothing.** No read is gated on it, no write is gated on
it, and a signed-out browser is served exactly what it was served before
accounts existed. What identity is for is owning a profile, and for the small
set of endpoints under /api/auth that administer accounts - those, and only
those, use `require_principal` / `require_admin` below.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass

from fastapi import Depends, Header, HTTPException, Request, Response
from sqlalchemy.orm import Session
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from app.db import get_db
from app.models import User, UserSession
from app.services import accounts
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


# --- who is asking (D25) ----------------------------------------------------


@dataclass(frozen=True)
class Principal:
    """A signed-in caller, and the session they are signed in on.

    The session travels with the user because two operations need to name *this*
    browser specifically: signing out ends this one, and changing a password
    ends every one except this one.
    """

    user: User
    session: UserSession


def current_principal(
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
) -> Principal | None:
    """The signed-in caller, or None. Never raises, never gates.

    Every page load calls this through GET /api/auth/session, so an anonymous
    reader must cost one indexed lookup and no exception. A cookie that is
    expired, revoked, unknown, or belongs to a deactivated account is simply
    not a caller - the browser is told to drop it by the router, not here.
    """
    raw = request.cookies.get(settings.session_cookie_name, "")
    resolved = accounts.resolve_session(db, raw, settings)
    if resolved is None:
        return None
    user, session = resolved
    return Principal(user=user, session=session)


def require_principal(principal: Principal | None = Depends(current_principal)) -> Principal:
    """401 for anyone not signed in.

    Used only by the endpoints that read or change an account. Putting this on
    a tender route would reverse D25 by accident.
    """
    if principal is None:
        raise HTTPException(status_code=401, detail="Sign in to do that.")
    return principal


def require_admin(principal: Principal = Depends(require_principal)) -> Principal:
    """403 for a signed-in member; 401 for a stranger, via the dependency above.

    The two are deliberately different: 401 means "identify yourself", 403 means
    "you have, and it is not enough". Collapsing them would have the dashboard
    show a sign-in form to someone already signed in.
    """
    if not principal.user.is_admin:
        raise HTTPException(status_code=403, detail="That needs an administrator account.")
    return principal
