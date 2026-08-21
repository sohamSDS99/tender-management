"""HTTP API."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import Select, distinct, func, or_, select
from sqlalchemy.orm import Session

from app.connectors.registry import SOURCE_NAMES, source_catalog
from app.db import get_db
from app.models import FetchRun, Tender, utcnow
from app.schemas import (
    FetchRequest,
    FetchResponse,
    FetchRunSchema,
    RescoreResponse,
    SortOption,
    SourceStatus,
    StatsResponse,
    TenderDetail,
    TenderPage,
)
from app.services import ingest
from app.services.relevance import get_engine
from app.settings import Settings, get_settings

router = APIRouter()

CLOSING_SOON_DAYS = 14


def settings_dep() -> Settings:
    return get_settings()


@router.get("/health", tags=["system"])
def health(db: Session = Depends(get_db)) -> dict[str, object]:
    db.execute(select(func.count(Tender.id))).scalar_one()
    return {"status": "ok", "time": utcnow().isoformat() + "Z"}


@router.get("/api/sources", response_model=list[SourceStatus], tags=["sources"])
def list_sources(
    db: Session = Depends(get_db), settings: Settings = Depends(settings_dep)
) -> list[SourceStatus]:
    counts = dict(db.execute(select(Tender.source, func.count(Tender.id)).group_by(Tender.source)).all())
    running = ingest.running_sources()
    out: list[SourceStatus] = []
    for entry in source_catalog(settings):
        name = str(entry["name"])
        last_run = db.execute(
            select(FetchRun).where(FetchRun.source == name).order_by(FetchRun.started_at.desc()).limit(1)
        ).scalar_one_or_none()
        last_success = db.execute(
            select(FetchRun.finished_at)
            .where(FetchRun.source == name, FetchRun.status.in_(("success", "partial")))
            .order_by(FetchRun.finished_at.desc())
            .limit(1)
        ).scalar_one_or_none()
        out.append(
            SourceStatus(
                **entry,
                tender_count=counts.get(name, 0),
                running=name in running,
                last_status=last_run.status if last_run else None,
                last_run_at=last_run.started_at if last_run else None,
                last_success_at=last_success,
                last_error=last_run.error_message if last_run else None,
            )
        )
    return out


def _apply_filters(
    stmt: Select,
    *,
    query: str | None,
    sources: Sequence[str] | None,
    countries: Sequence[str] | None,
    categories: Sequence[str] | None,
    statuses: Sequence[str] | None,
    fit_statuses: Sequence[str] | None,
    deployment_fits: Sequence[str] | None,
    minimum_score: int | None,
    maximum_score: int | None,
    published_from: datetime | None,
    published_to: datetime | None,
    deadline_from: datetime | None,
    deadline_to: datetime | None,
    active_only: bool,
    has_deadline: bool | None,
) -> Select:
    if query:
        like = f"%{query.strip()}%"
        stmt = stmt.where(
            or_(
                Tender.title.ilike(like),
                Tender.description.ilike(like),
                Tender.buyer_name.ilike(like),
                Tender.reference_number.ilike(like),
            )
        )
    if sources:
        stmt = stmt.where(Tender.source.in_(list(sources)))
    if countries:
        stmt = stmt.where(Tender.buyer_country.in_(list(countries)))
    if categories:
        stmt = stmt.where(Tender.relevance_category.in_(list(categories)))
    if statuses:
        stmt = stmt.where(Tender.status.in_(list(statuses)))
    if fit_statuses:
        stmt = stmt.where(Tender.fit_status.in_(list(fit_statuses)))
    if deployment_fits:
        stmt = stmt.where(Tender.deployment_fit.in_(list(deployment_fits)))
    if minimum_score is not None:
        stmt = stmt.where(Tender.relevance_score >= minimum_score)
    if maximum_score is not None:
        stmt = stmt.where(Tender.relevance_score <= maximum_score)
    if published_from:
        stmt = stmt.where(Tender.publication_date >= published_from)
    if published_to:
        stmt = stmt.where(Tender.publication_date <= published_to)
    if deadline_from:
        stmt = stmt.where(Tender.deadline >= deadline_from)
    if deadline_to:
        stmt = stmt.where(Tender.deadline <= deadline_to)
    if has_deadline is True:
        stmt = stmt.where(Tender.deadline.is_not(None))
    if has_deadline is False:
        stmt = stmt.where(Tender.deadline.is_(None))
    if active_only:
        stmt = stmt.where(
            Tender.is_actionable.is_(True),
            or_(Tender.deadline.is_(None), Tender.deadline >= utcnow()),
        )
    return stmt


_SORTS = {
    "score_desc": (Tender.relevance_score.desc(), Tender.deadline.is_(None), Tender.deadline.asc()),
    "score_asc": (Tender.relevance_score.asc(),),
    "deadline_asc": (Tender.deadline.is_(None), Tender.deadline.asc()),
    "deadline_desc": (Tender.deadline.is_(None), Tender.deadline.desc()),
    "published_desc": (Tender.publication_date.is_(None), Tender.publication_date.desc()),
    "published_asc": (Tender.publication_date.is_(None), Tender.publication_date.asc()),
    "first_seen_desc": (Tender.first_seen_at.desc(),),
}


@router.get("/api/tenders", response_model=TenderPage, tags=["tenders"])
def list_tenders(
    db: Session = Depends(get_db),
    query: str | None = Query(
        default=None, description="Free text over title, description, buyer, reference"
    ),
    sources: Annotated[list[str] | None, Query()] = None,
    countries: Annotated[list[str] | None, Query()] = None,
    categories: Annotated[list[str] | None, Query(description="Relevance profile keys")] = None,
    statuses: Annotated[list[str] | None, Query()] = None,
    fit_statuses: Annotated[list[str] | None, Query()] = None,
    deployment_fits: Annotated[list[str] | None, Query()] = None,
    minimum_score: int | None = Query(default=None, ge=0, le=100),
    maximum_score: int | None = Query(default=None, ge=0, le=100),
    published_from: datetime | None = None,
    published_to: datetime | None = None,
    deadline_from: datetime | None = None,
    deadline_to: datetime | None = None,
    active_only: bool = False,
    has_deadline: bool | None = None,
    sort: SortOption = "score_desc",
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=200),
) -> TenderPage:
    filters = dict(
        query=query,
        sources=sources,
        countries=countries,
        categories=categories,
        statuses=statuses,
        fit_statuses=fit_statuses,
        deployment_fits=deployment_fits,
        minimum_score=minimum_score,
        maximum_score=maximum_score,
        published_from=published_from,
        published_to=published_to,
        deadline_from=deadline_from,
        deadline_to=deadline_to,
        active_only=active_only,
        has_deadline=has_deadline,
    )
    total = db.execute(_apply_filters(select(func.count(Tender.id)), **filters)).scalar_one()
    stmt = _apply_filters(select(Tender), **filters).order_by(*_SORTS[sort], Tender.id.desc())
    rows = db.execute(stmt.offset((page - 1) * page_size).limit(page_size)).scalars().all()
    return TenderPage(
        items=rows,
        total=total,
        page=page,
        page_size=page_size,
        pages=max(1, (total + page_size - 1) // page_size),
    )


@router.get("/api/tenders/{tender_id}", response_model=TenderDetail, tags=["tenders"])
def get_tender(tender_id: int, db: Session = Depends(get_db)) -> Tender:
    row = db.get(Tender, tender_id)
    if row is None:
        raise HTTPException(status_code=404, detail="tender not found")
    return row


@router.post("/api/fetch", response_model=FetchResponse, status_code=202, tags=["fetch"])
async def trigger_fetch(
    payload: FetchRequest | None = None, settings: Settings = Depends(settings_dep)
) -> dict[str, object]:
    payload = payload or FetchRequest()
    if payload.sources:
        unknown = [s for s in payload.sources if s not in SOURCE_NAMES]
        if unknown:
            raise HTTPException(status_code=400, detail=f"unknown sources: {', '.join(unknown)}")
    return await ingest.start_fetch(payload.sources, payload.days_back, "manual", settings)


@router.get("/api/fetch-runs", response_model=list[FetchRunSchema], tags=["fetch"])
def list_fetch_runs(
    db: Session = Depends(get_db),
    source: str | None = None,
    status: str | None = None,
    limit: int = Query(default=50, ge=1, le=500),
) -> Sequence[FetchRun]:
    stmt = select(FetchRun).order_by(FetchRun.started_at.desc(), FetchRun.id.desc()).limit(limit)
    if source:
        stmt = stmt.where(FetchRun.source == source)
    if status:
        stmt = stmt.where(FetchRun.status == status)
    return db.execute(stmt).scalars().all()


@router.post("/api/tenders/rescore", response_model=RescoreResponse, tags=["tenders"])
def rescore(db: Session = Depends(get_db)) -> RescoreResponse:
    get_engine.cache_clear()  # pick up edits to relevance_profiles.yaml
    return RescoreResponse(rescored=ingest.rescore_all(db))


@router.get("/api/stats", response_model=StatsResponse, tags=["system"])
def stats(db: Session = Depends(get_db), settings: Settings = Depends(settings_dep)) -> StatsResponse:
    engine = get_engine()
    bands = engine.bands
    now = utcnow()

    def count(*where) -> int:
        return db.execute(select(func.count(Tender.id)).where(*where)).scalar_one()

    total = db.execute(select(func.count(Tender.id))).scalar_one()
    actionable_clause = (
        Tender.is_actionable.is_(True),
        or_(Tender.deadline.is_(None), Tender.deadline >= now),
    )
    by_source = [
        {"key": src, "count": n}
        for src, n in db.execute(
            select(Tender.source, func.count(Tender.id))
            .group_by(Tender.source)
            .order_by(func.count(Tender.id).desc())
        ).all()
    ]
    by_fit = [
        {"key": key or "unknown", "count": n}
        for key, n in db.execute(
            select(Tender.fit_status, func.count(Tender.id)).group_by(Tender.fit_status)
        ).all()
    ]
    labels = {p["key"]: p["label"] for p in engine.profile_metadata()}
    by_category = [
        {"key": key or "none", "label": labels.get(key or "", None), "count": n}
        for key, n in db.execute(
            select(Tender.relevance_category, func.count(Tender.id))
            .group_by(Tender.relevance_category)
            .order_by(func.count(Tender.id).desc())
        ).all()
    ]
    by_deployment = [
        {"key": key or "unknown", "count": n}
        for key, n in db.execute(
            select(Tender.deployment_fit, func.count(Tender.id)).group_by(Tender.deployment_fit)
        ).all()
    ]
    countries = [
        c
        for (c,) in db.execute(select(distinct(Tender.buyer_country)).order_by(Tender.buyer_country)).all()
        if c
    ]
    statuses = [s for (s,) in db.execute(select(distinct(Tender.status)).order_by(Tender.status)).all() if s]
    last_success = db.execute(
        select(func.max(FetchRun.finished_at)).where(FetchRun.status.in_(("success", "partial")))
    ).scalar_one_or_none()
    latest_runs = db.execute(
        select(FetchRun.source, func.max(FetchRun.started_at)).group_by(FetchRun.source)
    ).all()
    failed = 0
    for source, started in latest_runs:
        run = db.execute(
            select(FetchRun).where(FetchRun.source == source, FetchRun.started_at == started).limit(1)
        ).scalar_one_or_none()
        if run and run.status == "failed":
            failed += 1
    return StatsResponse(
        total_tenders=total,
        excellent_fit=count(Tender.relevance_score >= bands["excellent_fit"]),
        good_fit_or_better=count(Tender.relevance_score >= bands["good_fit"]),
        possible_or_review=count(
            Tender.relevance_score >= bands["possible_fit"],
            Tender.relevance_score < bands["good_fit"],
        ),
        not_relevant=count(Tender.relevance_score < bands["possible_fit"]),
        closing_soon=count(
            *actionable_clause,
            Tender.deadline.is_not(None),
            Tender.deadline <= now + timedelta(days=CLOSING_SOON_DAYS),
            Tender.relevance_score >= bands["possible_fit"],
        ),
        actionable=count(*actionable_clause),
        failed_sources=failed,
        last_successful_fetch=last_success,
        by_source=by_source,
        by_fit_status=by_fit,
        by_category=by_category,
        by_deployment=by_deployment,
        countries=countries,
        statuses=statuses,
        categories=[{"key": p["key"], "label": p["label"], "count": 0} for p in engine.profile_metadata()],
        score_bands=bands,
    )
