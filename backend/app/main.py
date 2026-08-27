"""FastAPI application entrypoint."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.docs import get_redoc_html, get_swagger_ui_html
from fastapi.responses import HTMLResponse

from app.api import auth_router, router
from app.db import SessionLocal, init_db
from app.logging_config import configure_logging
from app.security import SecurityHeadersMiddleware, enforce_sign_in
from app.services import automation
from app.services.scheduler import start_scheduler, stop_scheduler
from app.settings import get_settings

logger = logging.getLogger(__name__)

DESCRIPTION = """
Tender monitoring for **SDS management / SDS authoring / chemical compliance /
EHS software** opportunities.

Every notice is fetched from a free public source, normalized, de-duplicated and
scored by a deterministic, explainable relevance engine that also judges product
fit against a cloud-hosted SaaS delivery model.
"""


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    configure_logging(settings.log_level)
    init_db()
    # A previous process may have died mid-fetch, leaving runs marked running.
    # Close them out so the dashboard reports the truth.
    db = SessionLocal()
    try:
        automation.reap_interrupted_runs(db, settings)
    finally:
        db.close()
    start_scheduler(settings)
    try:
        yield
    finally:
        stop_scheduler()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Tender Monitor API",
        description=DESCRIPTION,
        version="0.1.0",
        lifespan=lifespan,
        # Applied to every route, including the docs and any added later, so a
        # new endpoint is private the moment it exists rather than the moment
        # somebody remembers. See app/security.py::enforce_sign_in and D26.
        dependencies=[Depends(enforce_sign_in)],
        # All three are re-registered below rather than left to FastAPI, and
        # that is not a style preference - see the note on _mount_docs.
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.add_middleware(SecurityHeadersMiddleware)
    origins = settings.cors_origin_list
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins or ["*"],
        # The session cookie only travels to an origin named explicitly. '*'
        # with credentials is rejected by every browser anyway, so deriving the
        # flag from whether an origin list exists is the only combination that
        # both works and stays safe when CORS_ORIGINS is cleared. Neither
        # supported deployment needs it - Vite proxies in development and the
        # web container proxies in production, so the dashboard is same-origin
        # with the API in both - but a browser pointed straight at the API with
        # VITE_API_BASE_URL set does. See docs/DECISIONS.md (D25).
        allow_credentials=bool(origins),
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(router)
    app.include_router(auth_router)
    if settings.enable_api_docs:
        _mount_docs(app)
    return app


def _mount_docs(app: FastAPI) -> None:
    """Serve /docs, /redoc and /openapi.json *behind the sign-in gate*.

    FastAPI's own docs routes are registered through Starlette's ``add_route``
    rather than ``add_api_route``, and application-level ``dependencies`` only
    reach the latter. So with ``docs_url="/docs"`` the gate silently does not
    apply to them: every other route answered 401 while ``/openapi.json`` handed
    a stranger the complete list of paths, parameters and schema names. Measured,
    not theorised - it was 200 on a running server while everything around it was
    401.

    Registering them here as ordinary API routes puts them inside the same
    dependency chain as everything else. ``include_in_schema=False`` keeps them
    out of the document they serve.

    Production sets ``ENABLE_API_DOCS=false`` and has no docs routes at all, so
    this closes the default rather than the deployment.
    """

    @app.get("/openapi.json", include_in_schema=False)
    def openapi_json() -> dict:
        return app.openapi()

    @app.get("/docs", include_in_schema=False)
    def swagger_ui() -> HTMLResponse:
        return get_swagger_ui_html(openapi_url="/openapi.json", title=f"{app.title} - Swagger UI")

    @app.get("/redoc", include_in_schema=False)
    def redoc_ui() -> HTMLResponse:
        return get_redoc_html(openapi_url="/openapi.json", title=f"{app.title} - ReDoc")


app = create_app()
