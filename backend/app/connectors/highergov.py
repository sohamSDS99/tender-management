"""HigherGov - US federal + state/local opportunities.

GET https://www.highergov.com/api-external/opportunity/
Requires HIGHERGOV_API_KEY (gear icon -> API in a HigherGov account; only
account admins can mint one) *and* HIGHERGOV_SEARCH_ID. Both are mandatory, and
the second one is the unusual part.

WHY A SAVED SEARCH IS NOT OPTIONAL
    This API has no free-text search. Not on this endpoint and not on any of
    the other eighteen: the OAS lists no keyword/query/text parameter anywhere.
    Worse, unknown parameters are *accepted and silently ignored* - measured
    2026-09-02, `q="safety data sheet"` and `q=zzzzznonsense` returned
    byte-identical result sets. So a connector that passes a keyword parameter
    looks like it is filtering while actually pulling the raw firehose.

    The only server-side filter is `search_id`: a search built in the HigherGov
    web UI, whose `searchID` is copied out of the URL. It carries Keywords,
    NAICS, PSC, Set Aside, Date Due and Value Range. The `opportunity` endpoint
    itself takes no naics_code/psc_code (unlike `contract`/`idv`), so there is
    no way to narrow the feed in code.

    Which makes the quota binding. Base usage is 10,000 records/month on every
    subscription, and a single day of postings was 5,538 records - an unfiltered
    date scan burns the monthly allowance in under two days and, measured
    against this repo's own relevance engine, returns nothing: 0 of 300 sampled
    records reached the 50-point review band. Hence the hard refusal below when
    HIGHERGOV_SEARCH_ID is unset. Running without it is not a degraded mode, it
    is a way to spend the whole quota on noise.

WHY ONE REQUEST INSTEAD OF ONE PER DAY
    `posted_date` accepts a single date only - a comma range answers HTTP 500,
    and `posted_date__gte` is one of the silently-ignored parameters - so
    covering an N-day window server-side would cost N requests. A
    precision-first saved search returns tens of records in total, so the whole
    search is fetched in one request and the window is applied in code. That
    keeps a sweep at one request regardless of lookback, which is what the
    monthly quota actually cares about.

    The window is matched against posted_date OR captured_date, because 15 of
    55 live records had them differ: a notice posted weeks ago can be captured
    by HigherGov today, and filtering on posted_date alone would drop it.

CREDENTIAL IN THE PAYLOAD
    Every record's `document_path` arrives with the API key already embedded as
    a query parameter. Stored verbatim it would put the key in the database and
    render it in the dashboard, so it is scrubbed out of both document_urls and
    raw_payload before the record leaves this module. See _scrub.
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

API_URL = "https://www.highergov.com/api-external/opportunity/"
#: Max accepted by the API; anything larger is clamped server-side.
MAX_PAGE_SIZE = 100
_API_KEY_QS = re.compile(r"((?:api_?key|token)=)[^&]+", re.IGNORECASE)
_ENTITIES = {"&amp;": "&", "&rsquo;": "'", "&nbsp;": " ", "&quot;": '"', "&#39;": "'"}


class HigherGovConnector(TenderConnector):
    source_name = "highergov"
    display_name = "HigherGov (US federal + SLED)"
    homepage = "https://www.highergov.com"
    requires_api_key = True
    # The saved search is a *relevance* filter, not a topical one: it is tuned
    # in the UI and can be as loose as whoever built it left it. On the live
    # search this connector was verified against, the title+buyer prefilter cut
    # 55 records to 5 and lost no record scoring >=50 - the 50 it dropped were
    # all chemical *purchase* notices whose text merely requires an SDS on
    # delivery. Same measured trade-off sam.py documents, so the same choice:
    # prefilter on title and buyer, never on the description.
    prefilter = True
    notes = (
        "Aggregates SAM, DIBBS, SBIR, grants and state/local. The API has no free-text "
        "search and silently ignores unknown parameters, so HIGHERGOV_SEARCH_ID (a saved "
        "search built in the HigherGov UI) is required - without it the only alternative "
        "is an unfiltered date scan that spends the 10,000-record monthly quota in under "
        "two days. One request per sweep; the date window is applied client-side because "
        "posted_date takes a single date and rejects ranges."
    )

    def unavailable_reason(self) -> str | None:
        if not self.settings.highergov_api_key:
            return (
                "HIGHERGOV_API_KEY is not set - an account admin can create one from the "
                "gear icon -> API at https://www.highergov.com"
            )
        if not self.settings.highergov_search_id:
            return (
                "HIGHERGOV_SEARCH_ID is not set - build a search at "
                "https://www.highergov.com and copy searchID from the URL. The API has no "
                "keyword parameter, so without a saved search there is nothing to filter on."
            )
        return None

    async def fetch(self, date_from: datetime, date_to: datetime) -> list[NormalizedTender]:
        date_from, date_to = self.clamp_window(date_from, date_to)
        window_from, window_to = date_from.date(), date_to.date()
        key = self.settings.highergov_api_key
        search_id = self.settings.highergov_search_id
        out: list[NormalizedTender] = []
        seen: set[str] = set()
        received = 0
        in_window = 0
        truncated = False

        async with self.client() as client:
            budget = max(1, self.settings.highergov_max_pages)
            for page in range(budget):
                params = {
                    "api_key": key,
                    "search_id": search_id,
                    "page_size": min(self.settings.page_size, MAX_PAGE_SIZE),
                    "page_number": page + 1,
                }
                data = await self.request(client, "GET", API_URL, params=params, expect="json")
                records = data.get("results") or []
                pagination = (data.get("meta") or {}).get("pagination") or {}
                received += len(records)
                for raw in records:
                    posted = _as_date(raw.get("posted_date"))
                    captured = _as_date(raw.get("captured_date"))
                    # Either date landing in the window keeps the record: a notice
                    # posted weeks ago can be captured today, and 15 of 55 live
                    # records had the two differ.
                    if not any(d and window_from <= d <= window_to for d in (posted, captured)):
                        continue
                    in_window += 1
                    agency = raw.get("agency") or {}
                    if not self.keep(raw.get("title"), agency.get("agency_name")):
                        continue
                    try:
                        tender = self._normalize(raw)
                    except Exception:
                        continue
                    if tender and tender.source_notice_id not in seen:
                        seen.add(tender.source_notice_id)
                        out.append(tender)
                pages = pagination.get("pages")
                if not records or not (data.get("links") or {}).get("next"):
                    break
                if page + 1 >= budget and isinstance(pages, int) and pages > budget:
                    # Never let a capped sweep read as full coverage.
                    truncated = True

        self.log_progress(
            received=received,
            in_window=in_window,
            kept=len(out),
            truncated=truncated,
        )
        return out

    def _normalize(self, raw: dict[str, Any]) -> NormalizedTender | None:
        opp_key = raw.get("opp_key")
        if not opp_key:
            return None
        posted, posted_tz = parse_datetime(raw.get("posted_date"), ("%Y-%m-%d",))
        captured, _ = parse_datetime(raw.get("captured_date"), ("%Y-%m-%d",))
        deadline, deadline_tz = parse_datetime(raw.get("due_date"), ("%Y-%m-%d",))

        codes: list[dict[str, Any]] = []
        naics = (raw.get("naics_code") or {}).get("naics_code")
        if naics:
            codes.append({"scheme": "NAICS", "code": str(naics)})
        psc = (raw.get("psc_code") or {}).get("psc_code")
        if psc:
            codes.append({"scheme": "PSC", "code": str(psc)})

        agency = raw.get("agency") or {}
        location = ", ".join(
            str(part)
            for part in (raw.get("pop_city"), raw.get("pop_state"), _country(raw.get("pop_country")))
            if part
        )
        notice_type = (raw.get("opp_type") or {}).get("description") or raw.get("source_type")
        # source_path is the buying authority's own listing; document_path is
        # HigherGov's document API and arrives with the key already in it.
        documents = [url for url in (_scrub(raw.get("source_path")), _scrub(raw.get("document_path"))) if url]
        return NormalizedTender(
            source=self.source_name,
            source_notice_id=str(opp_key),
            # `path` is already absolute - prefixing the host would corrupt it.
            source_url=_scrub(raw.get("path")),
            reference_number=raw.get("source_id") or raw.get("nsn") or None,
            title=_clean(raw.get("title")) or str(opp_key),
            description=_clean(raw.get("description_text") or raw.get("ai_summary")),
            buyer_name=agency.get("agency_name") or None,
            buyer_country="US",
            delivery_location=location or None,
            publication_date=posted,
            deadline=deadline,
            # What "new to us" means for an aggregator is when *it* saw the
            # notice, which is also what an incremental sweep should compare.
            source_updated_at=captured or posted,
            source_timezone=deadline_tz or posted_tz,
            status=status_from_deadline(deadline),
            procurement_stage=stage_from_code(notice_type),
            notice_type=str(notice_type) if notice_type else None,
            estimated_value=_amount(raw.get("val_est_high") or raw.get("val_est_low")),
            currency="USD",
            classification_codes=codes,
            document_urls=documents,
            language="en",
            raw_payload=_scrub_payload(raw),
        )


def _scrub(url: Any) -> str | None:
    """Strip a credential out of a source-supplied URL before it is stored.

    HigherGov embeds the caller's own api_key in every record's document_path.
    That value would otherwise be written to the database and rendered in the
    dashboard, so it never leaves this module intact.
    """
    if not url:
        return None
    return _API_KEY_QS.sub(r"\1***", str(url))


def _scrub_payload(raw: dict[str, Any]) -> dict[str, Any]:
    """The stored payload keeps every fact except the credential."""
    out = dict(raw)
    for field in ("document_path", "path", "source_path"):
        if out.get(field):
            out[field] = _scrub(out[field])
    return out


def _clean(text: Any) -> str | None:
    """HigherGov returns HTML entities inside otherwise-plain text fields."""
    if not text:
        return None
    out = str(text)
    for entity, char in _ENTITIES.items():
        out = out.replace(entity, char)
    return " ".join(out.split()) or None


def _as_date(value: Any) -> Any:
    parsed, _ = parse_datetime((str(value).strip() if value else None) or None, ("%Y-%m-%d",))
    return parsed.date() if parsed else None


def _country(value: Any) -> str | None:
    """`pop_country` is "USA"; the rest of the system stores ISO-2."""
    if not value:
        return None
    text = str(value).strip()
    return "US" if text.upper() in {"USA", "US", "UNITED STATES"} else text


def _amount(value: Any) -> float | None:
    """Estimated values arrive as strings ("18000"), not numbers."""
    if value in (None, "", "null"):
        return None
    try:
        return float(str(value).replace(",", ""))
    except ValueError:
        return None
