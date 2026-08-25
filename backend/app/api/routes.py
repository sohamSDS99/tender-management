"""HTTP API."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import Select, distinct, func, or_, select
from sqlalchemy.orm import Session

from app.connectors.registry import SOURCE_NAMES, source_catalog
from app.db import get_db
from app.jobs.schedule import next_run_local, utc_cron_expressions
from app.models import FetchRun, Tender, utcnow
from app.schemas import (
    AutomationStatus,
    CredentialRequest,
    FetchRequest,
    FetchResponse,
    FetchRunSchema,
    RescoreResponse,
    ScheduleResponse,
    ScheduleUpdate,
    SortOption,
    SourceStatus,
    StatsResponse,
    TenderDetail,
    TenderPage,
    TriggerResponse,
    TriggerUpdate,
)
from app.security import has_cron_secret
from app.services import automation, ingest, operator, schedule_settings, scheduler
from app.services.credentials import (
    CREDENTIAL_FIELDS,
    credential_hint,
    set_credential,
    settings_with_stored_credentials,
    stored_credential,
)
from app.services.matching_rules import (
    InvalidRules,
    clear_overrides,
    engine_for,
    read_rules,
    save_overrides,
)
from app.services.matching_rules import (
    preview as preview_rules,
)
from app.services.relevance import get_engine
from app.settings import Settings, get_settings

router = APIRouter()

CLOSING_SOON_DAYS = 14


def settings_dep() -> Settings:
    return get_settings()


@router.get("/health", tags=["system"])
def health(db: Session = Depends(get_db)) -> dict[str, object]:
    """Liveness plus a real database round-trip, naming the engine it reached."""
    tenders = db.execute(select(func.count(Tender.id))).scalar_one()
    return {
        "status": "ok",
        "time": utcnow().isoformat() + "Z",
        "database": {"dialect": db.get_bind().dialect.name, "ok": True, "tenders": tenders},
    }


@router.get("/api/automation", response_model=AutomationStatus, tags=["system"])
def automation_status(
    db: Session = Depends(get_db), settings: Settings = Depends(settings_dep)
) -> dict[str, object]:
    """Next scheduled run, last run's outcome and Slack health.

    This is what replaced the manual-fetch buttons: the dashboard can report the
    automation without being able to start it.
    """
    return automation.automation_status(db, settings)


@router.put(
    "/api/automation/schedule",
    response_model=ScheduleResponse,
    tags=["system"],
    summary="Set the times of day the sweep runs",
)
def set_schedule(
    payload: ScheduleUpdate,
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
) -> ScheduleResponse:
    """Change when the automated sweep runs.

    Deliberately *not* behind CRON_SECRET, unlike the other write endpoints. The
    reasoning is written up in docs/DECISIONS.md D19, and it turns on what this
    endpoint is: the schedule is an operating decision that a member of staff
    makes, and the person making it in the dashboard *is* the authorisation. The
    endpoints that stay gated are the ones a browser has no business triggering -
    POST /api/fetch spends outbound requests against eight public services, and
    /rescore rewrites every stored row.

    What this cannot do is bound the damage of a bad value, so the validation in
    schedule_settings.parse_hours is strict: 1-6 distinct hours in 0-23, rejected
    with a readable message rather than silently repaired.
    """
    try:
        hours = schedule_settings.set_run_hours(db, payload.hours_local, settings)
    except schedule_settings.InvalidSchedule as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None

    applied = scheduler.reschedule(hours, settings)
    tz = settings.scheduler_timezone
    if applied:
        detail = "The running scheduler was updated."
    elif not schedule_settings.get_enabled(db, settings):
        # Distinguish the two reasons nothing was rescheduled. "No scheduler runs
        # here" reads as "another process owns the trigger", which is the wrong
        # thing to tell someone who has simply paused the sweep.
        detail = "Saved. Sweeps are paused, so these times apply once you switch them back on."
    else:
        detail = (
            "Saved. No scheduler runs in this process, so it takes effect wherever the "
            "sweep is triggered from."
        )
    return ScheduleResponse(
        hours_local=hours,
        timezone=tz,
        cron_utc=utc_cron_expressions(tuple(hours), tz),
        next_run_local_label=next_run_local(None, tuple(hours), tz).strftime("%d %b %Y, %H:%M"),
        applied_to_running_scheduler=applied,
        detail=detail,
    )


@router.put(
    "/api/automation/trigger",
    response_model=TriggerResponse,
    tags=["system"],
    summary="Switch automated sweeps on or off",
)
async def set_trigger(
    payload: TriggerUpdate,
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
) -> TriggerResponse:
    """Pause or resume the automated sweep.

    Ungated for the same reason as the schedule endpoint: whether the sweep runs
    is an operating decision, and the member of staff making it in the dashboard
    *is* the authorisation (docs/DECISIONS.md D19, extended by D21). It spends no
    outbound requests and rewrites no rows - pausing spends strictly less than
    doing nothing.

    This deliberately supersedes D19's "a bad value cannot disable sweeps": that
    held while the only editable value was the hours. An operator who needs to
    stop the sweep - a source rate-limiting us, a maintenance window - had no way
    to, short of recreating the container. The guard is now visibility rather than
    prohibition: a paused system says so on the dashboard, unmissably, until it is
    switched back on.

    ``async def``, not sync: ``AsyncIOScheduler.start()`` binds to the running
    event loop, and a sync route would execute in a threadpool worker that has
    none - the switch would report success and never fire a sweep.
    """
    try:
        enabled = schedule_settings.set_enabled(db, payload.enabled, settings)
    except schedule_settings.InvalidTriggerState as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None

    hours = schedule_settings.get_run_hours(db, settings)
    running = scheduler.set_trigger(enabled, hours, settings)
    tz = settings.scheduler_timezone
    label = next_run_local(None, tuple(hours), tz).strftime("%d %b %Y, %H:%M") if enabled else None

    if not enabled:
        detail = (
            "Sweeps are paused. No notices will be collected and no digest will be sent "
            "until you switch them back on."
        )
    elif running:
        detail = f"Sweeps are on. The next one is {label} {tz}."
    else:
        # Asked for, but this process is not the trigger owner. Say so rather
        # than promising a run that will never fire (D2).
        detail = (
            "Saved. No scheduler runs in this process, so sweeps happen wherever the "
            "trigger is owned - check that it is switched on there."
        )

    return TriggerResponse(
        enabled=enabled,
        is_custom=schedule_settings.enabled_is_customised(db),
        default=schedule_settings.default_enabled(settings),
        scheduler_running=running,
        next_run_local_label=label,
        detail=detail,
    )


@router.get("/api/sources", response_model=list[SourceStatus], tags=["sources"])
def list_sources(
    db: Session = Depends(get_db), settings: Settings = Depends(settings_dep)
) -> list[SourceStatus]:
    counts = dict(db.execute(select(Tender.source, func.count(Tender.id)).group_by(Tender.source)).all())
    running = ingest.running_sources()
    out: list[SourceStatus] = []
    # With the stored credentials applied, so a key set from this very page stops
    # the source reporting "SAM_GOV_API_KEY is not set" the moment it is saved.
    # unavailable_reason() is computed from the settings the connector is built
    # with, so passing the raw ones would contradict the hint shown beside it.
    resolved = settings_with_stored_credentials(db, settings)
    for entry in source_catalog(resolved, db=db):
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
                credential_configured=stored_credential(db, name) is not None,
                credential_hint=credential_hint(db, name),
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
    first_seen_from: datetime | None,
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
    if first_seen_from:
        # "What arrived recently" - first_seen_at is written once at insert and
        # never touched again, so this is discovery time, not amendment time.
        stmt = stmt.where(Tender.first_seen_at >= first_seen_from)
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


@router.put(
    "/api/sources/{name}/credential",
    status_code=204,
    tags=["sources"],
    summary="Set or clear a source's API key - write-only, never read back",
)
def set_source_credential(
    name: str,
    payload: CredentialRequest,
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
) -> Response:
    """Store a credential so it takes effect without editing .env or restarting.

    Write-only on purpose. The dashboard is unauthenticated (D23), and D23's
    reasoning - these writes are expensive, not confidential, so rate-limit them
    rather than gate them - does not extend to a secret. Nothing about *reading*
    a key can be rate-limited, so the read path does not exist: GET /api/sources
    reports only whether one is set and its last four characters.

    Gated by ALLOW_OPERATOR_ACTIONS, reusing the switch the other operator
    writes already answer to rather than inventing an auth system.
    """
    if not settings.allow_operator_actions:
        raise HTTPException(
            status_code=403,
            detail=(
                "Editing credentials from the dashboard is switched off "
                "(ALLOW_OPERATOR_ACTIONS=false). Set it in .env instead."
            ),
        )
    if name not in CREDENTIAL_FIELDS:
        raise HTTPException(
            status_code=404,
            detail=f"'{name}' does not take an API key.",
        )
    set_credential(db, name, payload.value)
    return Response(status_code=204)


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
    first_seen_from: datetime | None = Query(
        default=None, description="Only notices first discovered at or after this instant"
    ),
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
        first_seen_from=first_seen_from,
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


@router.post(
    "/api/fetch",
    response_model=FetchResponse,
    status_code=202,
    tags=["fetch"],
    summary="Start a sweep - CI with X-Cron-Secret, or an operator under the cooldown",
)
async def trigger_fetch(
    payload: FetchRequest | None = None,
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
    trusted: bool = Depends(has_cron_secret),
) -> dict[str, object]:
    """Start a sweep now.

    Callable from the dashboard without a shared secret (D23). The secret was
    never protecting anything confidential here - reads are wide open (D5) - it
    was protecting eight public services from being hammered, so the guards in
    app/services/operator.py do that job directly: one sweep at a time, and a
    minimum gap between operator-initiated runs. A caller presenting
    CRON_SECRET is trusted and skips both, because CI controls its own schedule.
    """
    payload = payload or FetchRequest()
    if payload.sources:
        unknown = [s for s in payload.sources if s not in SOURCE_NAMES]
        if unknown:
            raise HTTPException(status_code=400, detail=f"unknown sources: {', '.join(unknown)}")
    if not trusted:
        operator.guard_fetch(db, settings)
    # An omitted window means "an operator asked for a sweep", not "repeat the
    # scheduled one". Those are different questions: the schedule's 72-hour
    # overlap keeps up with the present, so by the time a human clicks this it
    # contains nothing unseen and the sweep creates nothing while reporting
    # success. OPERATOR_FETCH_DAYS_BACK is the deeper window that makes the
    # button mean what it says; ingest.window() still enforces the 72-hour floor
    # underneath, so this can never search *less* than the schedule does.
    days_back = payload.days_back if payload.days_back is not None else settings.operator_fetch_days_back
    # An operator sweep is a sweep. Without a batch id its per-source rows only
    # group by a shared window_to - the compatibility fallback for rows written
    # before the column existed (D8) - and /api/automation cannot attribute
    # anything to it.
    batch_id = uuid4().hex[:16]
    started = await ingest.start_fetch(payload.sources, days_back, "manual", settings, batch_id=batch_id)
    # Merged here rather than returned by ingest: the window rule belongs to the
    # operator path, and app/services/ingest.py is additive-only.
    return {**started, "days_back": days_back, "batch_id": batch_id}


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


@router.post(
    "/api/tenders/rescore",
    response_model=RescoreResponse,
    tags=["tenders"],
    summary="Re-score every stored notice - CI with X-Cron-Secret, or an operator under the cooldown",
)
def rescore(
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
    trusted: bool = Depends(has_cron_secret),
) -> RescoreResponse:
    """Reload relevance_profiles.yaml and re-score everything.

    Spends no outbound request, but rewrites every stored row, so it carries its
    own cooldown rather than the sweep's (D23).
    """
    if not trusted:
        operator.guard_rescore(db, settings)
    get_engine.cache_clear()  # pick up edits to relevance_profiles.yaml
    rescored = ingest.rescore_all(db)
    operator.mark_rescore(db)
    return RescoreResponse(rescored=rescored)


@router.get("/api/matching-rules", tags=["rules"])
def get_matching_rules(db: Session = Depends(get_db)) -> dict[str, object]:
    """The tunable subset of relevance_profiles.yaml, with overrides applied."""
    return read_rules(db)


@router.post(
    "/api/matching-rules/preview",
    tags=["rules"],
    summary="What a rule change would move, without moving it",
)
def preview_matching_rules(payload: dict, db: Session = Depends(get_db)) -> dict:
    """Score the corpus under candidate rules and report the delta.

    Read-only: nothing is stored and no notice is rewritten. This exists so a
    re-score stops being a leap - someone may have been working a shortlist for
    a week under the current ranking.
    """
    try:
        return preview_rules(db, payload)
    except InvalidRules as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.put(
    "/api/matching-rules",
    response_model=RescoreResponse,
    tags=["rules"],
    summary="Change the matching rules and re-score",
)
def put_matching_rules(
    payload: dict,
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
    trusted: bool = Depends(has_cron_secret),
) -> RescoreResponse:
    """Store overrides, then re-score every notice under the new rules.

    The YAML file is never rewritten - overrides live in app_settings and merge
    over it, so the file's matching contract and its comments stay intact and
    "reset to defaults" is a row deletion.

    Carries the re-score cooldown, because that is what this actually does.
    """
    if not trusted:
        operator.guard_rescore(db, settings)
    try:
        save_overrides(db, payload)
    except InvalidRules as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    rescored = ingest.rescore_all(db)
    operator.mark_rescore(db)
    return RescoreResponse(rescored=rescored)


@router.delete(
    "/api/matching-rules",
    response_model=RescoreResponse,
    tags=["rules"],
    summary="Hand the matching rules back to the file and re-score",
)
def delete_matching_rules(
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
    trusted: bool = Depends(has_cron_secret),
) -> RescoreResponse:
    if not trusted:
        operator.guard_rescore(db, settings)
    clear_overrides(db)
    rescored = ingest.rescore_all(db)
    operator.mark_rescore(db)
    return RescoreResponse(rescored=rescored)


@router.get("/api/stats", response_model=StatsResponse, tags=["system"])
def stats(db: Session = Depends(get_db), settings: Settings = Depends(settings_dep)) -> StatsResponse:
    engine = engine_for(db)
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
