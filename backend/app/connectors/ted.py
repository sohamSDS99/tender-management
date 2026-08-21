"""EU TED (Tenders Electronic Daily) - Search API v3.

POST https://api.ted.europa.eu/v3/notices/search
Docs: https://docs.ted.europa.eu/api/latest/search.html
No authentication is required for published notices.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.connectors.base import (
    NormalizedTender,
    TenderConnector,
    first_text,
    join_text,
    parse_datetime,
    stage_from_code,
    status_from_deadline,
)
from app.connectors.keywords import SEARCH_PHRASES

API_URL = "https://api.ted.europa.eu/v3/notices/search"
NOTICE_URL = "https://ted.europa.eu/en/notice/-/detail/{}"

FIELDS = [
    "publication-number",
    "notice-identifier",
    "notice-title",
    "description-lot",
    "buyer-name",
    "buyer-country",
    "buyer-city",
    "classification-cpv",
    "publication-date",
    "deadline-receipt-tender-date-lot",
    "deadline-receipt-request-date-lot",
    "notice-type",
    "notice-subtype",
    "form-type",
    "procedure-type",
    "place-of-performance",
    "total-value",
    "total-value-cur",
    "estimated-value-lot",
    "estimated-value-cur-lot",
    "links",
]


class TedConnector(TenderConnector):
    source_name = "ted"
    display_name = "EU TED"
    homepage = "https://ted.europa.eu"
    notes = (
        'Expert-search full-text query (FT ~ "...") over the publication-date window. '
        "Notice stage is derived from the notice-type code (pin* planning, cn* tender, can* award)."
    )

    def _query(self, date_from: datetime, date_to: datetime) -> str:
        window = (
            f"publication-date>={date_from.strftime('%Y%m%d')} "
            f"AND publication-date<={date_to.strftime('%Y%m%d')}"
        )
        terms = " OR ".join(f'FT ~ "{phrase}"' for phrase in SEARCH_PHRASES)
        return f"({window}) AND ({terms})"

    async def fetch(self, date_from: datetime, date_to: datetime) -> list[NormalizedTender]:
        date_from, date_to = self.clamp_window(date_from, date_to)
        body: dict[str, Any] = {
            "query": self._query(date_from, date_to),
            "fields": FIELDS,
            "limit": min(self.settings.page_size, 250),
            "paginationMode": "ITERATION",
        }
        out: list[NormalizedTender] = []
        seen: set[str] = set()
        token: str | None = None
        async with self.client(headers={"Content-Type": "application/json"}) as client:
            for page in range(self.settings.max_pages_per_source):
                payload = dict(body)
                if token:
                    payload["iterationNextToken"] = token
                data = await self.request(client, "POST", API_URL, json=payload, expect="json")
                notices = data.get("notices") or []
                self.log_progress(
                    page=page + 1,
                    received=len(notices),
                    total=data.get("totalNoticeCount"),
                )
                for raw in notices:
                    try:
                        tender = self._normalize(raw)
                    except Exception:  # one bad record must not drop the page
                        continue
                    if tender and tender.source_notice_id not in seen:
                        seen.add(tender.source_notice_id)
                        out.append(tender)
                token = data.get("iterationNextToken")
                if not token or not notices:
                    break
        return out

    # -- normalization ------------------------------------------------------
    def _normalize(self, raw: dict[str, Any]) -> NormalizedTender | None:
        number = first_text(raw.get("publication-number"))
        if not number:
            return None
        title_map = raw.get("notice-title") or {}
        language = _preferred_language(title_map)
        title = _localized(title_map, language) or first_text(title_map) or number
        description = _localized(raw.get("description-lot"), language) or join_text(
            raw.get("description-lot")
        )

        publication_date, tz_label = parse_datetime(first_text(raw.get("publication-date")))
        deadline, deadline_tz = _earliest(
            list(_as_list(raw.get("deadline-receipt-tender-date-lot")))
            + list(_as_list(raw.get("deadline-receipt-request-date-lot")))
        )
        value, currency = _value(raw)
        notice_type = first_text(raw.get("notice-type")) or first_text(raw.get("form-type"))
        codes = [
            {"scheme": "CPV", "code": str(code)}
            for code in dict.fromkeys(str(c) for c in _as_list(raw.get("classification-cpv")))
        ]
        links = raw.get("links") or {}
        documents = [
            url
            for url in (
                first_text((links.get("xml") or {}).get("MUL")),
                _localized(links.get("pdf"), "ENG") or first_text(links.get("pdf")),
                _localized(links.get("html"), "ENG") or first_text(links.get("html")),
            )
            if url
        ]
        return NormalizedTender(
            source=self.source_name,
            source_notice_id=number,
            source_url=NOTICE_URL.format(number),
            reference_number=first_text(raw.get("notice-identifier")) or number,
            title=title,
            description=description,
            buyer_name=first_text(raw.get("buyer-name")),
            buyer_country=(first_text(raw.get("buyer-country")) or "").upper() or None,
            delivery_location=first_text(raw.get("place-of-performance"))
            or first_text(raw.get("buyer-city")),
            publication_date=publication_date,
            deadline=deadline,
            source_updated_at=publication_date,
            source_timezone=deadline_tz or tz_label,
            status=status_from_deadline(deadline),
            procurement_stage=stage_from_code(notice_type),
            notice_type=notice_type,
            estimated_value=value,
            currency=currency,
            classification_codes=codes,
            document_urls=documents,
            language=(language or "").lower()[:3] or None,
            raw_payload=raw,
        )


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        out: list[Any] = []
        for item in value.values():
            out.extend(_as_list(item))
        return out
    return [value]


def _preferred_language(mapping: Any) -> str | None:
    if not isinstance(mapping, dict):
        return None
    for key in ("eng", "ENG", "en"):
        if key in mapping:
            return key
    return next(iter(mapping), None)


def _localized(mapping: Any, language: str | None) -> str | None:
    if not isinstance(mapping, dict) or not language:
        return None
    if language in mapping:
        return join_text(mapping[language])
    return None


def _earliest(values: list[Any]) -> tuple[datetime | None, str | None]:
    parsed = [parse_datetime(first_text(v)) for v in values]
    candidates = [(dt, tz) for dt, tz in parsed if dt]
    if not candidates:
        return None, None
    return min(candidates, key=lambda pair: pair[0])


def _value(raw: dict[str, Any]) -> tuple[float | None, str | None]:
    for value_key, cur_key in (
        ("total-value", "total-value-cur"),
        ("estimated-value-lot", "estimated-value-cur-lot"),
    ):
        for candidate in _as_list(raw.get(value_key)):
            try:
                amount = float(str(candidate).replace(",", ""))
            except (TypeError, ValueError):
                continue
            if amount > 0:
                return amount, first_text(raw.get(cur_key))
    return None, first_text(raw.get("total-value-cur")) or first_text(raw.get("estimated-value-cur-lot"))
