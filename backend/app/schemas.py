"""API request/response schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_serializer

from app.models.tender_feedback import IRRELEVANT

Verdict = Literal["relevant", "irrelevant"]

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


class FeedbackOut(UtcModel):
    """A reviewer's verdict on one notice, or absent when nobody has decided."""

    verdict: Verdict
    note: str | None
    created_at: datetime
    updated_at: datetime


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

    # --- reviewer feedback and what was learned from it (D26) ---------------
    #: What a person decided, if anyone has. Nested rather than flattened
    #: because the note and the timestamp travel with the verdict, and the card
    #: needs the verdict while the detail panel needs all three.
    feedback: FeedbackOut | None = None
    #: The learner's own call, and the patterns behind it. Never a substitute
    #: for a verdict: it is recomputed on every re-score and defers to one.
    auto_irrelevant: bool = False
    auto_irrelevant_reasons: list[str] = Field(default_factory=list)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def hidden(self) -> bool:
        """Excluded from the working views - by a person, or by the learner.

        The one definition the browser is allowed to read. It is mirrored in SQL
        by ``feedback.marked_irrelevant_subquery`` plus the ``auto_irrelevant``
        column, because a filter has to run in the database while a response is
        assembled here; ``test_feedback.py`` asserts the two agree across the
        whole corpus, which is what stops them drifting apart.
        """
        return self.auto_irrelevant or (self.feedback is not None and self.feedback.verdict == IRRELEVANT)


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
    #: Whether a key is stored for this source. The value itself is never
    #: returned by any endpoint - see app/services/credentials.py.
    credential_configured: bool = False
    #: Last four characters, for confirming *which* key is set.
    credential_hint: str | None = None
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
    # Every count below excludes hidden notices, so a lens count always equals
    # the list that lens opens - the default view hides them, and a facet number
    # beside a narrowed list that counted a different population is the exact
    # failure this rule exists to prevent.
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
    #: The one count that *is* the hidden population, so the Not-relevant lens
    #: has an honest badge and the difference from total_tenders is explainable.
    hidden_total: int = 0


class RescoreResponse(BaseModel):
    rescored: int


class FeedbackRequest(BaseModel):
    verdict: Verdict
    note: str | None = Field(default=None, max_length=2000)


class LearnedPattern(BaseModel):
    """One phrase the system worked out for itself, with its evidence."""

    phrase: str
    #: Log-odds. Higher means more concentrated in what was rejected.
    weight: float
    marked: int
    elsewhere: int


class LearnedModel(BaseModel):
    """What the learner currently knows, and whether it is allowed to act.

    ``active`` is false until ``marks_needed`` reaches zero. Reporting the two
    separately matters: "no patterns yet" and "not enough marks to trust any"
    are different states, and only the second is fixed by marking more notices.
    """

    active: bool
    marks_irrelevant: int
    marks_relevant: int
    marks_needed: int
    corpus: int
    hidden_total: int
    hidden_by_learning: int
    hidden_by_hand: int
    patterns: list[LearnedPattern]
    thresholds: dict[str, float]


class FeedbackResponse(BaseModel):
    """The answer to marking one notice: the verdict, and what it changed.

    ``reclassified`` is the feature working out loud - mark one notice not
    relevant and this says how many others the system just stopped showing you.
    Without it, a mark that quietly hid thirty rows would look like a bug.
    """

    tender_id: int
    verdict: Verdict | None
    reclassified: int
    learned: LearnedModel


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


class CredentialRequest(BaseModel):
    """A new credential. Blank clears it and falls back to the environment."""

    value: str = Field(default="", max_length=512)


class ProbeRequest(BaseModel):
    """A candidate source, before it is anything."""

    url: str = Field(max_length=2000)
    auth: str = "none"
    auth_param: str | None = Field(default=None, max_length=128)
    credential: str = Field(default="", max_length=512)
    mapping: dict[str, str] | None = None


class SourceRequest(BaseModel):
    """A source to create. The credential is set separately, write-only."""

    name: str = Field(min_length=2, max_length=64, pattern=r"^[a-z0-9_]+$")
    display_name: str = Field(min_length=1, max_length=200)
    url: str = Field(max_length=2000)
    homepage: str = Field(default="", max_length=500)
    auth: str = "none"
    auth_param: str | None = Field(default=None, max_length=128)
    format: str = "json"
    mapping: dict[str, str] | None = None
    notes: str = ""
    credential: str = Field(default="", max_length=512)


# --- accounts (D25) ---------------------------------------------------------
#
# Nothing in this group appears inside a tender response. Accounts are a
# parallel surface, not a field on the data, which is what keeps a signed-out
# reader's payloads byte-for-byte what they were before D25.


class UserOut(UtcModel):
    """A person, as the dashboard is allowed to see them.

    No password hash, no lockout counters, no session tokens. The admin user
    list and "who am I" both render from this one shape, so there is no second
    definition to fall out of step.
    """

    id: int
    email: str
    display_name: str
    role: str
    is_active: bool
    created_at: datetime
    last_login_at: datetime | None


class SessionState(UtcModel):
    """What one call at page load has to answer.

    The dashboard needs three things before it can draw the account control, and
    fetching them separately would flash the wrong state in between: who you
    are, whether an invite is needed to register, and whether this deployment is
    still waiting for its first administrator.
    """

    user: UserOut | None
    #: True only while no account exists. The next registration takes the
    #: dashboard's first admin slot, which is why the UI says so out loud.
    bootstrap: bool
    #: The mirror of bootstrap, named for what the form needs rather than for
    #: the condition, because that is what the form asks.
    invite_required: bool


class RegisterRequest(BaseModel):
    email: str
    password: str
    display_name: str = ""
    #: Required except on the bootstrap registration. Comes from ?invite= in the
    #: link an administrator sent.
    invite_token: str | None = None


class LoginRequest(BaseModel):
    email: str
    password: str


class ProfileUpdate(BaseModel):
    """Both fields optional: a PATCH that names one must not blank the other."""

    display_name: str | None = None
    email: str | None = None


class PasswordChange(BaseModel):
    current_password: str
    new_password: str


class SessionOut(UtcModel):
    """One live session in the profile's list."""

    id: int
    created_at: datetime
    last_seen_at: datetime
    expires_at: datetime
    user_agent: str
    #: The browser making this request. Marked so nobody ends their own session
    #: expecting it to be one of the others.
    current: bool


class InviteOut(UtcModel):
    id: int
    email: str | None
    role: str
    note: str
    #: pending / accepted / expired / revoked, derived rather than stored.
    status: str
    created_at: datetime
    expires_at: datetime
    accepted_at: datetime | None


class InviteCreate(BaseModel):
    email: str | None = None
    role: str = "member"
    note: str = ""


class InviteCreated(UtcModel):
    """The one response in the API that carries a live credential.

    The token is returned exactly once, at creation, because only its SHA-256 is
    stored. An administrator who loses the link issues another one; there is no
    endpoint that can show it again.
    """

    invite: InviteOut
    token: str
    url: str


class UserAdminUpdate(BaseModel):
    role: str | None = None
    is_active: bool | None = None


class RevokedCount(BaseModel):
    revoked: int
