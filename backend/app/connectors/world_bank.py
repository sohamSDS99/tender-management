"""World Bank procurement notices.

GET https://search.worldbank.org/api/procnotices
Offset/row pagination (``os`` / ``rows``) with the documented ``qterm`` keyword
search; contract awards are dropped in favour of active opportunities.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from app.connectors.base import (
    STAGE_AWARD,
    STAGE_TENDER,
    ConnectorError,
    NormalizedTender,
    TenderConnector,
    parse_datetime,
    status_from_deadline,
)

API_URL = "https://search.worldbank.org/api/procnotices"
DETAIL_URL = "https://projects.worldbank.org/en/projects-operations/procurement-detail/{}"
_TAGS = re.compile(r"<[^>]+>")

# `qterm` takes one phrase per request, so a compact list keeps the request
# count low while covering the company's capability areas.
QUERY_TERMS: tuple[str, ...] = (
    "safety data sheet",
    "chemical management",
    "chemical inventory",
    "hazardous substances management",
    "EHS management system",
    "occupational health and safety management system",
    "incident management system",
    "environmental management information system",
    "safety management software",
    "audit management software",
)
AWARD_TYPES = {"contract award", "contract awards"}


class WorldBankConnector(TenderConnector):
    source_name = "world_bank"
    display_name = "World Bank"
    homepage = "https://projects.worldbank.org"
    notes = (
        "One request per keyword (documented `qterm` parameter) with os/rows pagination. "
        "Contract awards and drafts are dropped; records are kept when published in the "
        "window or still open for submission."
    )

    async def fetch(self, date_from: datetime, date_to: datetime) -> list[NormalizedTender]:
        date_from, date_to = self.clamp_window(date_from, date_to)
        rows = min(self.settings.page_size, 100)
        pages = max(1, min(self.settings.max_pages_per_source // len(QUERY_TERMS), 3))
        now = datetime.now(UTC).replace(tzinfo=None)
        out: list[NormalizedTender] = []
        seen: set[str] = set()
        errors: list[ConnectorError] = []
        async with self.client() as client:
            for term in QUERY_TERMS:
                for page in range(pages):
                    params = {"format": "json", "rows": rows, "os": page * rows, "qterm": term}
                    try:
                        data = await self.request(client, "GET", API_URL, params=params, expect="json")
                    except ConnectorError as exc:
                        errors.append(exc)
                        self.log_progress(term=term, error=exc.message)
                        break
                    records = data.get("procnotices") or []
                    self.log_progress(
                        term=term, page=page + 1, received=len(records), total=data.get("total")
                    )
                    for raw in records:
                        try:
                            tender = self._normalize(raw)
                        except Exception:
                            continue
                        if not tender or tender.source_notice_id in seen:
                            continue
                        if tender.procurement_stage == STAGE_AWARD:
                            continue
                        if str(raw.get("notice_status", "")).lower() not in ("published", "active"):
                            continue
                        in_window = (
                            tender.publication_date is not None
                            and date_from <= tender.publication_date <= date_to
                        )
                        still_open = tender.deadline is not None and tender.deadline >= now
                        if not (in_window or still_open):
                            continue
                        seen.add(tender.source_notice_id)
                        out.append(tender)
                    if len(records) < rows:
                        break
        if errors and not out:
            raise errors[0]
        return out

    def _normalize(self, raw: dict[str, Any]) -> NormalizedTender | None:
        notice_id = raw.get("id")
        if not notice_id:
            return None
        published, published_tz = parse_datetime(raw.get("noticedate"), ("%d-%b-%Y", "%Y-%m-%d"))
        deadline_raw = raw.get("submission_deadline_date") or raw.get("submission_date")
        deadline, deadline_tz = parse_datetime(deadline_raw)
        notice_type = str(raw.get("notice_type") or "")
        stage = STAGE_AWARD if notice_type.lower() in AWARD_TYPES else STAGE_TENDER
        description_parts = [
            raw.get("bid_description"),
            raw.get("project_name"),
            _strip_html(raw.get("notice_text")),
        ]
        description = " | ".join(p for p in description_parts if p) or None
        return NormalizedTender(
            source=self.source_name,
            source_notice_id=str(notice_id),
            source_url=DETAIL_URL.format(notice_id),
            reference_number=raw.get("bid_reference_no") or raw.get("project_id"),
            title=str(raw.get("bid_description") or raw.get("project_name") or notice_id)[:1000],
            description=description[:20000] if description else None,
            buyer_name=raw.get("contact_organization") or raw.get("project_name"),
            buyer_country=(raw.get("project_ctry_name") or raw.get("contact_ctry_name") or None),
            delivery_location=raw.get("project_ctry_name"),
            publication_date=published,
            deadline=deadline,
            source_updated_at=published,
            source_timezone=deadline_tz or published_tz,
            status=str(raw.get("notice_status") or "") or status_from_deadline(deadline),
            procurement_stage=stage,
            notice_type=notice_type or None,
            estimated_value=None,
            currency=None,
            classification_codes=(
                [{"scheme": "WB-PROCUREMENT-GROUP", "code": str(raw["procurement_group"])}]
                if raw.get("procurement_group")
                else []
            ),
            document_urls=[],
            language=(raw.get("notice_lang_name") or "English")[:16],
            raw_payload=raw,
        )


def _strip_html(text: Any) -> str | None:
    if not text:
        return None
    cleaned = _TAGS.sub(" ", str(text))
    cleaned = cleaned.replace("&#39;", "'").replace("&nbsp;", " ").replace("&amp;", "&")
    return re.sub(r"\s+", " ", cleaned).strip()[:20000] or None
