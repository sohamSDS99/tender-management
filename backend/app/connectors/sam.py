"""US SAM.gov - Get Opportunities API v2.

GET https://api.sam.gov/opportunities/v2/search
Requires a free personal API key (SAM_GOV_API_KEY). The connector disables
itself gracefully when the key is missing. Keys are never logged.

The daily quota is the binding constraint here, not the window: a key on a
non-federal account with no role gets 10 requests a day. ``sam_max_pages`` and
``sam_max_description_fetches`` are what keep a sweep inside that, and they
default to one request in total. See app/settings/config.py.
"""

from __future__ import annotations

import csv
import io
import re
import tempfile
from datetime import datetime
from typing import IO, Any

from app.connectors.base import (
    ConnectorError,
    NormalizedTender,
    TenderConnector,
    parse_datetime,
    stage_from_code,
    status_from_deadline,
)

API_URL = "https://api.sam.gov/opportunities/v2/search"
#: The daily "every active opportunity" extract. No API key, no login, no quota.
#: The 303 here redirects to a presigned S3 URL, so redirects must be followed,
#: and S3 rejects the request without a browser-shaped User-Agent - the same WAF
#: behaviour CanadaBuys and AusTender already need the default USER_AGENT for.
EXTRACT_URL = (
    "https://sam.gov/api/prod/fileextractservices/v1/api/download/"
    "Contract%20Opportunities/datagov/ContractOpportunitiesFullCSV.csv?privacy=Public"
)
#: The extract carries every notice type; these four are what the API asked for
#: with ptype=o,p,k,r, so filtering to them keeps the two transports comparable.
EXTRACT_NOTICE_TYPES = frozenset(
    {
        "Solicitation",
        "Presolicitation",
        "Combined Synopsis/Solicitation",
        "Sources Sought",
    }
)
UI_URL = "https://sam.gov/opp/{}/view"
# Documented ptype codes: p=Presolicitation, o=Solicitation,
# k=Combined Synopsis/Solicitation, r=Sources Sought.
DEFAULT_NOTICE_TYPES = "o,p,k,r"
_TAGS = re.compile(r"<[^>]+>")


class SamGovConnector(TenderConnector):
    source_name = "sam"
    display_name = "US SAM.gov"
    homepage = "https://sam.gov"
    prefilter = True
    notes = (
        "Get Opportunities v2, paginated with limit/offset over postedFrom/postedTo. "
        "SAM meters this API per day - 10 requests on a role-less non-federal account, "
        "1000 with a role - so a sweep is capped at SAM_MAX_PAGES pages (1) and "
        "SAM_MAX_DESCRIPTION_FETCHES per-notice description fetches (0). Without a "
        "description, a notice is scored on its title and contracting path alone."
    )

    @property
    def requires_api_key(self) -> bool:
        """Only the metered API needs one. The bulk extract is entirely open."""
        return not self.settings.sam_use_bulk_extract

    def unavailable_reason(self) -> str | None:
        if self.settings.sam_use_bulk_extract:
            return None
        if not self.settings.sam_gov_api_key:
            return "SAM_GOV_API_KEY is not set - request a free key at https://sam.gov/content/api-keys"
        return None

    async def fetch(self, date_from: datetime, date_to: datetime) -> list[NormalizedTender]:
        date_from, date_to = self.clamp_window(date_from, date_to)
        if self.settings.sam_use_bulk_extract:
            return await self._fetch_extract(date_from, date_to)
        return await self._fetch_api(date_from, date_to)

    async def _fetch_extract(self, date_from: datetime, date_to: datetime) -> list[NormalizedTender]:
        """One keyless file instead of a metered, paginated API.

        Three things make this the better transport despite the download size:
        there is no quota, the description arrives inline instead of costing a
        second request per notice, and no credential is involved at all.

        It is streamed to a temporary file and then parsed with the stdlib csv
        module rather than parsed incrementally off the socket. That is
        deliberate: descriptions are free text and routinely contain embedded
        newlines inside quoted fields, so splitting the stream on newlines
        yields corrupt rows. Spooling first costs disk and buys correctness.
        """
        out: list[NormalizedTender] = []
        seen: set[str] = set()
        rows = 0
        matched = 0
        window_from, window_to = date_from.date(), date_to.date()
        with tempfile.TemporaryFile() as spool:
            received = await self._download_extract(spool)
            spool.seek(0)
            # newline="" is required by the csv module to honour quoted newlines.
            text = io.TextIOWrapper(spool, encoding="utf-8", errors="replace", newline="")
            for row in csv.DictReader(text):
                rows += 1
                if (row.get("Type") or "").strip() not in EXTRACT_NOTICE_TYPES:
                    continue
                posted = _extract_date(row.get("PostedDate"))
                if posted is None or not (window_from <= posted <= window_to):
                    continue
                matched += 1
                # Title and contracting path only, exactly as the API path
                # prefilters. The description is right here and it is tempting to
                # add it, but measured on a live sample it took the pass rate
                # from 0.9% to 5.2% of candidates - 6x the rows - and every one
                # of the 29 extra notices was junk: forklift rental, fire door
                # replacement, grounds maintenance. Federal notices mention
                # "safety" or "hazard" somewhere in 2.6 KB of boilerplate almost
                # by default. The description still earns its keep by being
                # stored and *scored* for whatever the prefilter admits.
                if not self.keep(row.get("Title"), row.get("Sub-Tier")):
                    continue
                try:
                    raw = _from_extract_row(row)
                    tender = self._normalize(raw, _strip_html(row.get("Description")))
                except Exception:
                    continue
                if tender and tender.source_notice_id not in seen:
                    seen.add(tender.source_notice_id)
                    out.append(tender)
            self.log_progress(
                transport="bulk_extract",
                bytes_received=received,
                rows=rows,
                in_window=matched,
                kept=len(out),
            )
        return out

    async def _download_extract(self, sink: IO[bytes]) -> int:
        """Stream the extract into ``sink``, refusing to grow without bound."""
        limit = self.settings.sam_extract_max_bytes
        received = 0
        async with self.client() as client:
            async with client.stream("GET", EXTRACT_URL) as response:
                if response.status_code >= 400:
                    raise ConnectorError(
                        self.source_name,
                        f"HTTP {response.status_code} from source",
                        status=response.status_code,
                        url=EXTRACT_URL,
                        retryable=response.status_code >= 500,
                    )
                async for chunk in response.aiter_bytes():
                    received += len(chunk)
                    if received > limit:
                        raise ConnectorError(
                            self.source_name,
                            f"bulk extract exceeded {limit} bytes",
                            url=EXTRACT_URL,
                        )
                    sink.write(chunk)
        return received

    async def _fetch_api(self, date_from: datetime, date_to: datetime) -> list[NormalizedTender]:
        key = self.settings.sam_gov_api_key
        limit = min(self.settings.page_size * 5, 1000)
        out: list[NormalizedTender] = []
        seen: set[str] = set()
        descriptions_fetched = 0
        async with self.client() as client:
            # Not max_pages_per_source: that budget is per *sweep* everywhere else,
            # and here the ceiling is a daily quota shared with every other run.
            for page in range(max(1, self.settings.sam_max_pages)):
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
                            and descriptions_fetched < self.settings.sam_max_description_fetches
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


def _extract_date(value: str | None) -> Any:
    """``PostedDate`` is a plain date; return it as one, or None."""
    parsed, _ = parse_datetime((value or "").strip() or None, ("%Y-%m-%d",))
    return parsed.date() if parsed else None


def _from_extract_row(row: dict[str, str]) -> dict[str, Any]:
    """Rewrite a CSV row into the shape ``_normalize`` already understands.

    The two transports carry the same facts under different names, so the
    mapping lives here and normalisation stays a single code path - there is no
    second version of it to drift. ``NoticeId`` is the API's ``noticeId``, which
    is what lets a notice ingested by either transport upsert onto the same row
    rather than duplicating.
    """

    def val(column: str) -> str | None:
        return (row.get(column) or "").strip() or None

    # The API returned "DEPT OF DEFENSE.DEPT OF THE ARMY"; the extract splits
    # that across three columns.
    path = ".".join(part for part in (val("Department/Ind.Agency"), val("Sub-Tier"), val("Office")) if part)
    return {
        "noticeId": val("NoticeId"),
        "title": val("Title"),
        "solicitationNumber": val("Sol#"),
        "fullParentPathName": path or None,
        "postedDate": val("PostedDate"),
        "responseDeadLine": val("ResponseDeadLine"),
        "naicsCode": val("NaicsCode"),
        "classificationCode": val("ClassificationCode"),
        "placeOfPerformance": {
            "city": {"name": val("PopCity")},
            "state": {"name": val("PopState")},
            "country": {"name": val("PopCountry")},
        },
        # The extract publishes no attachment list; the API's resourceLinks has
        # no counterpart here, so document_urls is whatever this one link gives.
        "resourceLinks": [],
        "additionalInfoLink": val("AdditionalInfoLink"),
        "type": val("Type"),
        "baseType": val("BaseType"),
        "active": val("Active"),
        "uiLink": val("Link"),
        "organizationType": val("OrganizationType"),
        # Kept whole so the stored payload is auditable against the source file.
        "bulkExtractRow": {k: v for k, v in row.items() if v},
    }


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
