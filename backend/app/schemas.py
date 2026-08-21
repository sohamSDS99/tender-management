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
    first_seen_at: datetime
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


class FetchResponse(UtcModel):
    runs: list[dict[str, Any]]
    run_ids: list[int]
    skipped_sources: list[str]
    window_from: datetime
    window_to: datetime


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
