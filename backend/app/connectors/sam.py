"""US SAM.gov - Get Opportunities API v2.

GET https://api.sam.gov/opportunities/v2/search
Requires a free personal API key (SAM_GOV_API_KEY). The connector disables
itself gracefully when the key is missing. Keys are never logged.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from app.connectors.base import (
    NormalizedTender,
    TenderConnector,
    parse_datetime,
    stage_from_code,
    status_from_deadline,
)

API_URL = "https://api.sam.gov/opportunities/v2/search"
UI_URL = "https://sam.gov/opp/{}/view"
# Documented ptype codes: p=Presolicitation, o=Solicitation,
# k=Combined Synopsis/Solicitation, r=Sources Sought.
DEFAULT_NOTICE_TYPES = "o,p,k,r"
MAX_DESCRIPTION_FETCHES = 60
_TAGS = re.compile(r"<[^>]+>")


class SamGovConnector(TenderConnector):
    source_name = "sam"
    display_name = "US SAM.gov"
    homepage = "https://sam.gov"
    requires_api_key = True
    prefilter = True
    notes = (
        "Get Opportunities v2, paginated with limit/offset over postedFrom/postedTo. "
        "Descriptions live behind a per-notice link and are only fetched for notices "
        "that pass the topical prefilter."
    )

    def unavailable_reason(self) -> str | None:
        if not self.settings.sam_gov_api_key:
            return "SAM_GOV_API_KEY is not set - request a free key at https://sam.gov/content/api-keys"
        return None

    async def fetch(self, date_from: datetime, date_to: datetime) -> list[NormalizedTender]:
        date_from, date_to = self.clamp_window(date_from, date_to)
        key = self.settings.sam_gov_api_key
        limit = min(self.settings.page_size * 5, 1000)
        out: list[NormalizedTender] = []
        seen: set[str] = set()
        descriptions_fetched = 0
        async with self.client() as client:
            for page in range(self.settings.max_pages_per_source):
                params = {
                    "api_key": key,
                    "postedFrom": date_from.strftime("%m/%d/%Y"),
                    "postedTo": date_to.strftime("%m/%d/%Y"),
                    "limit": limit,
                    "offset": page * limit,
                    "ptype": DEFAULT_NOTICE_TYPES,
                }
                data = await self.request(client, "GET", API_URL, params=params, expect="json")
                records = data.get("opportunitiesData") or []
                total = data.get("totalRecords")
                self.log_progress(page=page + 1, received=len(records), total=total)
                for raw in records:
                    try:
                        notice_id = raw.get("noticeId")
                        if not notice_id or notice_id in seen:
                            continue
                        if not self.keep(raw.get("title"), raw.get("fullParentPathName")):
                            continue
                        description = raw.get("description")
                        if (
                            isinstance(description, str)
                            and description.startswith("http")
                            and descriptions_fetched < MAX_DESCRIPTION_FETCHES
                        ):
                            descriptions_fetched += 1
                            description = await self._description(client, description, key)
                        tender = self._normalize(raw, description)
                    except Exception:
                        continue
                    if tender:
                        seen.add(tender.source_notice_id)
                        out.append(tender)
                if not records or (isinstance(total, int) and (page + 1) * limit >= total):
                    break
        return out

    async def _description(self, client: Any, url: str, key: str) -> str | None:
        try:
            data = await self.request(client, "GET", url, params={"api_key": key}, expect="json")
        except Exception:
            return None
        if isinstance(data, dict):
            text = data.get("description") or data.get("body")
            return _strip_html(text) if text else None
        if isinstance(data, str):
            return _strip_html(data)
        return None

    def _normalize(self, raw: dict[str, Any], description: str | None) -> NormalizedTender | None:
        notice_id = raw.get("noticeId")
        if not notice_id:
            return None
        posted, posted_tz = parse_datetime(raw.get("postedDate"), ("%Y-%m-%d",))
        deadline, deadline_tz = parse_datetime(raw.get("responseDeadLine"))
        codes: list[dict[str, Any]] = []
        for code in _naics(raw):
            codes.append({"scheme": "NAICS", "code": code})
        if raw.get("classificationCode"):
            codes.append({"scheme": "PSC", "code": str(raw["classificationCode"])})
        place = raw.get("placeOfPerformance") or {}
        location = ", ".join(
            str(part)
            for part in (
                (place.get("city") or {}).get("name"),
                (place.get("state") or {}).get("name"),
                (place.get("country") or {}).get("name"),
            )
            if part
        )
        documents = [str(u) for u in (raw.get("resourceLinks") or []) if u]
        if raw.get("additionalInfoLink"):
            documents.append(str(raw["additionalInfoLink"]))
        notice_type = raw.get("type") or raw.get("baseType")
        active = str(raw.get("active", "")).lower()
        status = (
            "open" if active == "yes" else ("closed" if active == "no" else status_from_deadline(deadline))
        )
        return NormalizedTender(
            source=self.source_name,
            source_notice_id=str(notice_id),
            source_url=raw.get("uiLink") or UI_URL.format(notice_id),
            reference_number=raw.get("solicitationNumber"),
            title=str(raw.get("title") or notice_id)[:1000],
            description=description if isinstance(description, str) else None,
            buyer_name=raw.get("fullParentPathName") or (raw.get("organizationType") or None),
            buyer_country="US",
            delivery_location=location or None,
            publication_date=posted,
            deadline=deadline,
            source_updated_at=posted,
            source_timezone=deadline_tz or posted_tz,
            status=status,
            procurement_stage=stage_from_code(notice_type),
            notice_type=str(notice_type) if notice_type else None,
            estimated_value=None,
            currency="USD",
            classification_codes=codes,
            document_urls=documents[:25],
            language="en",
            raw_payload=raw,
        )


def _naics(raw: dict[str, Any]) -> list[str]:
    codes: list[str] = []
    if raw.get("naicsCode"):
        codes.append(str(raw["naicsCode"]))
    for entry in raw.get("naicsCodes") or []:
        if isinstance(entry, dict):
            for value in entry.get("code") or []:
                codes.append(str(value))
        elif entry:
            codes.append(str(entry))
    return list(dict.fromkeys(codes))


def _strip_html(text: str | None) -> str | None:
    if not text:
        return None
    return _TAGS.sub(" ", str(text)).replace("&nbsp;", " ").strip() or None
