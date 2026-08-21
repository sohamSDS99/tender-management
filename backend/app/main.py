"""FastAPI application entrypoint."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import router
from app.db import SessionLocal, init_db
from app.logging_config import configure_logging
from app.security import SecurityHeadersMiddleware
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
        docs_url="/docs" if settings.enable_api_docs else None,
        redoc_url="/redoc" if settings.enable_api_docs else None,
        openapi_url="/openapi.json" if settings.enable_api_docs else None,
    )
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list or ["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(router)
    return app


app = create_app()
