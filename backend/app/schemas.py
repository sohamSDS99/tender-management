"""API request/response schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_serializer

SortOption = Literal[
    "score_desc",
    "score_asc",
    "deadline_asc",
    "deadline_desc",
    "published_desc",
    "published_asc",
    "first_seen_desc",
]


class UtcModel(BaseModel):
    """Every datetime in the database is naive UTC; emit it as an explicit UTC instant."""

    model_config = ConfigDict(from_attributes=True)

    @field_serializer("*", when_used="json")
    def _utc(self, value: Any) -> Any:
        if isinstance(value, datetime) and value.tzinfo is None:
            return value.isoformat() + "Z"
        return value


class TenderListItem(UtcModel):
    id: int
    source: str
    source_notice_id: str
    source_url: str | None
    reference_number: str | None
    title: str
    buyer_name: str | None
    buyer_country: str | None
    publication_date: datetime | None
    deadline: datetime | None
    status: str | None
    procurement_stage: str | None
    notice_type: str | None
    estimated_value: float | None
    currency: str | None
    relevance_score: int
    relevance_category: str | None
    fit_status: str
    deployment_fit: str
    relevance_reasons: list[str] = Field(default_factory=list)
    disqualifiers: list[str] = Field(default_factory=list)
    review_flags: list[str] = Field(default_factory=list)
    is_actionable: bool
    last_seen_at: datetime
    # The dashboard marks a tender "New" when this is at or after the last run's
    # start, so the list needs it too - not just the detail view.
    first_seen_at: datetime


class TenderDetail(TenderListItem):
    description: str | None
    delivery_location: str | None
    classification_codes: list[dict[str, Any]] = Field(default_factory=list)
    document_urls: list[str] = Field(default_factory=list)
    language: str | None
    topic_relevance_score: int
    product_fit_score: int
    procurement_intent_score: int
    source_updated_at: datetime | None
    source_timezone: str | None
    content_hash: str
    created_at: datetime
    updated_at: datetime
    raw_payload: dict[str, Any] | None


class TenderPage(BaseModel):
    items: list[TenderListItem]
    total: int
    page: int
    page_size: int
    pages: int


class FetchRequest(BaseModel):
    sources: list[str] | None = Field(default=None, description="Source names; omit for every enabled source")
    days_back: int | None = Field(
        default=None, ge=0, le=365, description="Lookback window in days (minimum 72h is enforced)"
    )


class FetchRunSchema(UtcModel):
    id: int
    source: str
    started_at: datetime
    finished_at: datetime | None
    status: str
    records_received: int
    records_created: int
    records_updated: int
    records_skipped: int
    error_message: str | None
    window_from: datetime | None
    window_to: datetime | None
    trigger: str
    # Groups the per-source runs of one sweep; correlates with
    # slack_notifications.run_batch_id.
    batch_id: str | None


class FetchResponse(UtcModel):
    runs: list[dict[str, Any]]
    run_ids: list[int]
    skipped_sources: list[str]
    window_from: datetime
    window_to: datetime
    # How deep this sweep is searching, in days. Reported rather than assumed:
    # the dashboard says it on screen, because "found nothing" means something
    # very different over three days than over thirty.
    days_back: int
    # Groups this sweep's per-source runs, exactly as a scheduled sweep's are.
    batch_id: str | None = None


class SourceStatus(UtcModel):
    name: str
    display_name: str
    homepage: str
    enabled: bool
    requires_api_key: bool
    unavailable_reason: str | None
    keyword_prefiltered: bool
    notes: str
    tender_count: int = 0
    running: bool = False
    last_status: str | None = None
    last_run_at: datetime | None = None
    last_success_at: datetime | None = None
    last_error: str | None = None


class CountBucket(BaseModel):
    key: str
    label: str | None = None
    count: int


class StatsResponse(UtcModel):
    total_tenders: int
    excellent_fit: int
    good_fit_or_better: int
    possible_or_review: int
    not_relevant: int
    closing_soon: int
    actionable: int
    failed_sources: int
    last_successful_fetch: datetime | None
    by_source: list[CountBucket]
    by_fit_status: list[CountBucket]
    by_category: list[CountBucket]
    by_deployment: list[CountBucket]
    countries: list[str]
    statuses: list[str]
    categories: list[CountBucket]
    score_bands: dict[str, int]


class RescoreResponse(BaseModel):
    rescored: int


class SlackState(BaseModel):
    status: str
    detail: str | None = None
    sent_total: int = 0
    # Announcements that left this system without a confirmed reply from Slack.
    unconfirmed: int = 0
    sent_in_last_batch: int = 0
    channel_label: str | None = None
    min_score: int | None = None
    #: Which delivery path is in force: "bot_token", "webhook" or "none".
    transport: str = "none"


class LastRunError(BaseModel):
    source: str
    message: str


class LastRun(UtcModel):
    batch_id: str | None
    trigger: str
    status: str
    started_at: datetime
    finished_at: datetime | None
    started_at_local_label: str
    sources_total: int
    sources_failed: int
    records_received: int
    records_created: int
    records_updated: int
    errors: list[LastRunError] = Field(default_factory=list)


class ScheduledJob(UtcModel):
    id: str
    next_run_at: datetime | None


class AutomationStatus(UtcModel):
    """Read-only replacement for the removed manual-fetch controls."""

    public_app_url: str
    timezone: str
    run_hours_local: list[int]
    run_hours_are_custom: bool
    run_hours_min: int
    run_hours_max: int
    # How deep a sweep started from the dashboard looks, in days. Reported so the
    # page states it at the point of action instead of keeping a second copy of
    # the number: "found nothing" over three days and over thirty are different
    # facts, and only one of them is worth acting on.
    operator_fetch_days_back: int
    cron_utc: list[str]
    observes_dst: bool
    next_run_at: datetime
    next_run_local_label: str
    scheduler_in_process: bool
    scheduler_running: bool
    scheduler_jobs: list[ScheduledJob] = Field(default_factory=list)
    trigger_is_custom: bool = False
    trigger_default: bool = False
    trigger_changed_at: datetime | None = None
    last_run: LastRun | None
    slack: SlackState


class ScheduleUpdate(BaseModel):
    """A new sweep schedule, as local hours in the configured timezone."""

    hours_local: list[int] = Field(
        description="Local hours of day, 0-23. e.g. [0, 12] for midnight and midday.",
        examples=[[0, 12]],
    )


class ScheduleResponse(BaseModel):
    hours_local: list[int]
    timezone: str
    cron_utc: list[str]
    next_run_local_label: str
    applied_to_running_scheduler: bool
    detail: str


class TriggerUpdate(BaseModel):
    """Whether the sweep is triggered automatically at all."""

    enabled: bool = Field(
        description="true runs sweeps at the configured times; false pauses every sweep.",
        examples=[True],
    )


class TriggerResponse(BaseModel):
    enabled: bool
    is_custom: bool
    default: bool
    scheduler_running: bool
    next_run_local_label: str | None
    detail: str
