"""Common connector interface + shared HTTP plumbing.

Every connector normalizes into :class:`NormalizedTender`, keeps the raw source
record, retries only temporary failures, honours HTTP 429 ``Retry-After`` and
never logs credentials.
"""

from __future__ import annotations

import asyncio
import email.utils
import hashlib
import json
import logging
import re
from abc import ABC, abstractmethod
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import httpx
from pydantic import BaseModel, Field

from app.connectors.keywords import looks_relevant
from app.logging_config import log_ctx
from app.settings import Settings

logger = logging.getLogger(__name__)

RETRYABLE_STATUS = {408, 425, 429, 500, 502, 503, 504}
MAX_RETRY_AFTER_SECONDS = 120.0

STAGE_PLANNING = "planning"
STAGE_TENDER = "tender"
STAGE_AWARD = "award"


class ConnectorError(Exception):
    """Structured connector failure."""

    def __init__(
        self,
        source: str,
        message: str,
        *,
        status: int | None = None,
        url: str | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.source = source
        self.message = message
        self.status = status
        self.url = url
        self.retryable = retryable

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "message": self.message,
            "status": self.status,
            "url": _redact(self.url),
            "retryable": self.retryable,
        }

    def __str__(self) -> str:  # pragma: no cover - trivial
        parts = [self.message]
        if self.status:
            parts.append(f"status={self.status}")
        if self.url:
            parts.append(f"url={_redact(self.url)}")
        return " ".join(parts)


_SECRET_QS = re.compile(r"((?:api_?key|key|token|password)=)[^&]+", re.IGNORECASE)


def _redact(value: str | None) -> str | None:
    """Strip credentials out of anything that may reach a log or the database."""
    if not value:
        return value
    return _SECRET_QS.sub(r"\1***", value)


class NormalizedTender(BaseModel):
    """Source-agnostic tender record."""

    source: str
    source_notice_id: str
    source_url: str | None = None
    reference_number: str | None = None
    title: str = ""
    description: str | None = None
    buyer_name: str | None = None
    buyer_country: str | None = None
    delivery_location: str | None = None
    publication_date: datetime | None = None
    deadline: datetime | None = None
    source_updated_at: datetime | None = None
    source_timezone: str | None = None
    status: str | None = None
    procurement_stage: str | None = None
    notice_type: str | None = None
    estimated_value: float | None = None
    currency: str | None = None
    classification_codes: list[dict[str, Any]] = Field(default_factory=list)
    document_urls: list[str] = Field(default_factory=list)
    language: str | None = None
    raw_payload: dict[str, Any] = Field(default_factory=dict)

    @property
    def content_hash(self) -> str:
        """Hash of the fields that mean "this notice changed"."""
        payload = {
            "title": self.title,
            "description": self.description,
            "buyer_name": self.buyer_name,
            "buyer_country": self.buyer_country,
            "delivery_location": self.delivery_location,
            "publication_date": _iso(self.publication_date),
            "deadline": _iso(self.deadline),
            "status": self.status,
            "procurement_stage": self.procurement_stage,
            "notice_type": self.notice_type,
            "estimated_value": self.estimated_value,
            "currency": self.currency,
            "classification_codes": sorted(
                f"{c.get('scheme', '')}:{c.get('code', '')}" for c in self.classification_codes
            ),
            "document_urls": sorted(self.document_urls),
            "source_url": self.source_url,
            "reference_number": self.reference_number,
        }
        blob = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()


@dataclass
class FetchResult:
    tenders: list[NormalizedTender] = field(default_factory=list)
    skipped: int = 0  # malformed individual records
    warnings: list[str] = field(default_factory=list)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


# --- datetime helpers ------------------------------------------------------


def _tz_label(text: str) -> str | None:
    """Best-effort source timezone label (kept alongside the UTC value)."""
    if text.endswith("Z"):
        return "UTC"
    m = re.search(r"([+-]\d{2}:\d{2})$", text)
    if m:
        return m.group(1)
    m = re.search(r"\d{2}:\d{2}(?::\d{2})?\s?([+-]\d{4})$", text)
    if m:
        return m.group(1)
    m = re.search(r"\s([A-Z]{2,4})$", text)
    return m.group(1) if m else None


def to_utc_naive(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


def parse_datetime(value: Any, formats: Iterable[str] = ()) -> tuple[datetime | None, str | None]:
    """Parse a source date/datetime into (UTC-naive datetime, source tz label)."""
    if value in (None, "", "null"):
        return None, None
    if isinstance(value, datetime):
        tz = value.tzname() if value.tzinfo else None
        return to_utc_naive(value), tz
    text = str(value).strip()
    if not text:
        return None, None
    tz_label = _tz_label(text)
    candidate = text.replace("Z", "+00:00")
    # "2025-08-01+02:00" (date + offset, as returned by TED)
    date_offset = re.match(r"^(\d{4}-\d{2}-\d{2})([+-]\d{2}:?\d{2})$", candidate)
    if date_offset:
        candidate = f"{date_offset.group(1)}T00:00:00{date_offset.group(2)}"
    try:
        return to_utc_naive(datetime.fromisoformat(candidate)), tz_label
    except ValueError:
        pass
    for fmt in formats:
        try:
            return to_utc_naive(datetime.strptime(text, fmt)), tz_label
        except ValueError:
            continue
    return None, tz_label


def first_text(value: Any) -> str | None:
    """Flatten TED-style {lang: [str]} / nested list structures to one string."""
    if value is None:
        return None
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, int | float):
        return str(value)
    if isinstance(value, list):
        for item in value:
            text = first_text(item)
            if text:
                return text
        return None
    if isinstance(value, dict):
        for key in ("eng", "en", "ENG", "EN"):
            if key in value:
                text = first_text(value[key])
                if text:
                    return text
        for item in value.values():
            text = first_text(item)
            if text:
                return text
    return None


def join_text(value: Any, limit: int = 20000) -> str | None:
    """Collect all strings from a nested structure (one language preferred)."""
    if isinstance(value, dict):
        for key in ("eng", "en", "ENG", "EN"):
            if key in value:
                return join_text(value[key], limit)
        parts = [join_text(v, limit) for v in value.values()]
    elif isinstance(value, list):
        parts = [join_text(v, limit) for v in value]
    else:
        return first_text(value)
    text = " ".join(p for p in parts if p)
    return text[:limit] or None


# --- connector base --------------------------------------------------------


class TenderConnector(ABC):
    """Interface every source implements."""

    source_name: str = ""
    display_name: str = ""
    homepage: str = ""
    requires_api_key: bool = False
    notes: str = ""
    # Sources with no server-side keyword search are prefiltered client-side so
    # we do not store an entire national tender feed. See keywords.PREFILTER_TERMS.
    prefilter: bool = False

    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.settings = settings
        self.transport = transport

    # -- availability -------------------------------------------------------
    def unavailable_reason(self) -> str | None:
        """Non-None means the connector must be skipped gracefully."""
        return None

    @property
    def enabled(self) -> bool:
        return self.settings.source_enabled(self.source_name)

    def keep(self, *texts: str | None) -> bool:
        """Client-side topical prefilter (only for `prefilter = True` sources)."""
        if not self.prefilter or not self.settings.apply_keyword_prefilter:
            return True
        return looks_relevant(*texts)

    # -- http ---------------------------------------------------------------
    def client(self, **kwargs: Any) -> httpx.AsyncClient:
        headers = {"User-Agent": self.settings.user_agent, "Accept-Encoding": "gzip, deflate"}
        headers.update(kwargs.pop("headers", {}) or {})
        timeout = kwargs.pop("timeout", None) or self.settings.request_timeout_seconds
        return httpx.AsyncClient(
            timeout=httpx.Timeout(timeout),
            transport=self.transport,
            follow_redirects=True,
            headers=headers,
            **kwargs,
        )

    async def _sleep(self, seconds: float) -> None:
        if seconds > 0:
            await asyncio.sleep(seconds)

    async def request(
        self,
        client: httpx.AsyncClient,
        method: str,
        url: str,
        *,
        expect: Literal["json", "text", "xml", "csv"] = "json",
        content_types: tuple[str, ...] = (),
        **kwargs: Any,
    ) -> Any:
        """Perform a request with retry/backoff, 429 handling and size limits."""
        attempts = max(1, self.settings.max_retries + 1)
        last_error: ConnectorError | None = None
        for attempt in range(attempts):
            try:
                response = await client.request(method, url, **kwargs)
            except httpx.HTTPError as exc:
                last_error = ConnectorError(
                    self.source_name, f"transport error: {type(exc).__name__}: {exc}", url=url, retryable=True
                )
            else:
                if response.status_code in RETRYABLE_STATUS:
                    wait = self._retry_after(response) or self.settings.retry_backoff_seconds * (2**attempt)
                    last_error = ConnectorError(
                        self.source_name,
                        f"HTTP {response.status_code} from source",
                        status=response.status_code,
                        url=url,
                        retryable=True,
                    )
                    log_ctx(
                        logger,
                        logging.WARNING,
                        "retrying source request",
                        source=self.source_name,
                        status=response.status_code,
                        attempt=attempt + 1,
                        wait=round(wait, 2),
                    )
                    if attempt < attempts - 1:
                        await self._sleep(min(wait, MAX_RETRY_AFTER_SECONDS))
                        continue
                    raise last_error
                if response.status_code >= 400:
                    raise ConnectorError(
                        self.source_name,
                        f"HTTP {response.status_code}: {response.text[:300]}",
                        status=response.status_code,
                        url=url,
                    )
                return self._parse(response, expect, content_types)
            if attempt < attempts - 1:
                await self._sleep(self.settings.retry_backoff_seconds * (2**attempt))
        raise last_error or ConnectorError(self.source_name, "request failed", url=url)

    def _retry_after(self, response: httpx.Response) -> float | None:
        raw = response.headers.get("Retry-After")
        if not raw:
            return None
        raw = raw.strip()
        if raw.isdigit():
            return float(raw)
        try:
            when = email.utils.parsedate_to_datetime(raw)
        except (TypeError, ValueError):
            return None
        if when is None:
            return None
        if when.tzinfo is None:
            when = when.replace(tzinfo=UTC)
        return max(0.0, (when - datetime.now(UTC)).total_seconds())

    def _parse(self, response: httpx.Response, expect: str, content_types: tuple[str, ...]) -> Any:
        content_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
        allowed = content_types or _DEFAULT_CONTENT_TYPES[expect]
        if allowed and content_type and not any(a in content_type for a in allowed):
            raise ConnectorError(
                self.source_name,
                f"unexpected content-type '{content_type}' (expected one of {', '.join(allowed)})",
                status=response.status_code,
                url=str(response.request.url),
            )
        body = response.content
        if len(body) > self.settings.max_response_bytes:
            raise ConnectorError(
                self.source_name,
                f"response too large ({len(body)} bytes > {self.settings.max_response_bytes})",
                url=str(response.request.url),
            )
        if not body:
            # e.g. PNCP answers 204/empty when a page has no results.
            return {} if expect == "json" else ""
        if expect == "csv":
            return body.decode("utf-8-sig", errors="replace")
        if expect == "json":
            try:
                return response.json()
            except ValueError as exc:
                raise ConnectorError(
                    self.source_name, f"invalid JSON: {exc}", url=str(response.request.url)
                ) from exc
        return response.text

    # -- window helper ------------------------------------------------------
    @staticmethod
    def clamp_window(date_from: datetime, date_to: datetime) -> tuple[datetime, datetime]:
        if date_from > date_to:
            return date_to, date_from
        return date_from, date_to

    @abstractmethod
    async def fetch(self, date_from: datetime, date_to: datetime) -> list[NormalizedTender]:
        """Fetch and normalize notices updated/published in the window."""
        raise NotImplementedError

    # -- shared normalization helpers --------------------------------------
    def log_progress(self, **context: Any) -> None:
        log_ctx(logger, logging.INFO, "source progress", source=self.source_name, **context)

    @staticmethod
    def window_days(date_from: datetime, date_to: datetime) -> int:
        return max(1, (date_to - date_from) // timedelta(days=1))


_DEFAULT_CONTENT_TYPES: dict[str, tuple[str, ...]] = {
    "json": ("json",),
    "text": ("text", "json", "xml"),
    "xml": ("xml", "text", "rss"),
    "csv": ("csv", "text", "octet-stream", "excel"),
}


def status_from_deadline(deadline: datetime | None, now: datetime | None = None) -> str | None:
    """Generic open/closed status for sources that publish no explicit status."""
    if deadline is None:
        return None
    now = now or datetime.now(UTC).replace(tzinfo=None)
    return "open" if deadline >= now else "closed"


def stage_from_code(code: str | None) -> str:
    """Map a source notice-type code onto planning / tender / award."""
    text = (code or "").lower()
    if text.startswith(("pin", "plan")) or "prior information" in text or "planning" in text:
        return STAGE_PLANNING
    if text.startswith(("can", "award")) or "award" in text or "contract award" in text:
        return STAGE_AWARD
    return STAGE_TENDER
