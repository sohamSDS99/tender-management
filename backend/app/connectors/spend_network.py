"""Spend Network - Open Opportunities: one login over ~40 national portals.

    POST https://api.spendnetwork.cloud/api/v3/login/access-token   (email + password)
    GET  https://api.spendnetwork.cloud/api/v3/notices/records_openopps

Requires SPEND_NETWORK_EMAIL *and* SPEND_NETWORK_PASSWORD. Unlike every other
source here the credential is an account, not a key: there is no API key to
mint, so the connector signs in and carries a bearer token.

WHAT THIS ADDS THAT THE OTHER NINE DO NOT
    It is an aggregator, and a wide one. A single 31-day window of this repo's
    own keyword list returned notices from fifteen upstream portals across
    fourteen countries - German DTVP and oeffentlichevergabe, French BOAMP,
    achatpublic and marchesonline, Irish and Northern Irish eTenders, Canadian
    bidsandtenders, AusTender, PhilGEPS, US opengov - most of which this system
    has no connector for and several of which publish no usable feed at all.
    Every record is OCDS-shaped and carries an `ocid`, so the overlap with TED
    and Find a Tender deduplicates on content rather than accumulating.

THE SEARCH SYNTAX IS THE WHOLE DESIGN (all measured 2026-09-15)
    `search_term__is` is a real server-side search - `zzzzznonsensequery`
    returns 0 - but its default is OR-of-words, not phrase. Unquoted,
    `safety data sheet` matched 4,770 notices in a fortnight (safety=1,648,
    data=2,688, sheet=946, minus overlap) and its top hit was an ID-printer
    consumables tender. Quoted, `"safety data sheet"` matched 23. Word order is
    ignored unquoted; `sheet data safety` returned the identical 4,770.

    So every phrase is quoted. `OR` between quoted phrases works and is
    additive: `"safety data sheet"` (188) OR `"chemical management"` (9)
    returned 196, with `AND` confirming the single overlap. That is what lets
    the entire SEARCH_PHRASES list ride in one request instead of one request
    per phrase, which matters because of the throttle below.

    Unknown query parameters are accepted and silently ignored - a made-up
    parameter changed nothing - so only parameters that appear in the published
    OpenAPI document are sent, and none of them is trusted to have filtered
    without having been measured.

WHY THE WINDOW IS WIDENED RATHER THAN OBEYED
    The only date filter is `release_date`, which is when the *upstream portal*
    published - not when Spend Network ingested it. A notice released weeks ago
    can be aggregated today (`date_created` was the sweep day for all 97 records
    sampled, against release dates spread over a month), and a sweep windowed
    strictly on release_date would never see it. There is no `date_created`
    filter to use instead. So the lookback is widened by
    SPEND_NETWORK_BACKFILL_DAYS and the extra records are left to deduplicate in
    ingest, where a re-seen notice costs one unchanged row. It is affordable
    precisely because the keyword filter is precise: 97 records for a whole
    month, 23 for a week.

THE THROTTLE IS THE REAL BUDGET
    The account tier meters requests, and exhaustion is not an API error: it is
    a bare nginx `403 Forbidden` HTML page with no Retry-After, no rate-limit
    headers and no JSON. Twenty-five requests in quick succession tripped it,
    and - this is the part worth knowing - *every request made while blocked
    extended the block*. Polling it once every twenty seconds kept it closed for
    four minutes; leaving it alone for about ninety seconds cleared it.

    Hence: requests are paced, the page budget is small, and a 403 is answered
    with one long wait and one retry, never a poll. If the retry is also refused
    the sweep stops and returns what it already has, logged as truncated, rather
    than digging the hole deeper.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from app.connectors.base import (
    ConnectorError,
    NormalizedTender,
    TenderConnector,
    parse_datetime,
    stage_from_code,
    status_from_deadline,
)
from app.connectors.keywords import SEARCH_PHRASES
from app.logging_config import log_ctx

logger = logging.getLogger(__name__)

API_BASE = "https://api.spendnetwork.cloud"
LOGIN_URL = f"{API_BASE}/api/v3/login/access-token"
RECORDS_URL = f"{API_BASE}/api/v3/notices/records_openopps"

#: Server maximums. `limit` is clamped at 100 and `offset` refuses anything over
#: 9900, so no single query can reach past 10,000 records however it is paged.
MAX_PAGE_SIZE = 100
MAX_OFFSET = 9900

#: Tokens last eight days; this margin re-logs in before one expires mid-sweep.
TOKEN_EXPIRY_MARGIN = timedelta(hours=6)

#: Process-local token cache, keyed by a digest of the credentials so rotating
#: either one invalidates it. The token itself never reaches a log line.
_TOKEN_CACHE: dict[str, tuple[str, datetime]] = {}
_TOKEN_LOCK = asyncio.Lock()


class SpendNetworkConnector(TenderConnector):
    source_name = "spend_network"
    display_name = "Spend Network (Open Opportunities)"
    homepage = "https://www.spendnetwork.com"
    requires_api_key = True
    # The search runs on the server and is precise, so there is nothing for the
    # client-side prefilter to save: it would only re-apply a coarser version of
    # the filter that has already run. Same reasoning as TED.
    prefilter = False
    notes = (
        "An aggregator: one account covers ~40 national portals (German DTVP, French BOAMP, "
        "Irish eTenders, PhilGEPS, AusTender and more) that have no connector of their own. "
        "Needs an email and a password rather than an API key. Search phrases are quoted "
        "because the API ORs bare words - unquoted, one phrase matched 4,770 notices in a "
        "fortnight and quoted it matched 23. The account is metered and answers a bare HTML "
        "403 when it runs out, so requests are paced and a refusal stops the sweep rather "
        "than retrying into it."
    )

    def unavailable_reason(self) -> str | None:
        if not self.settings.spend_network_email:
            return (
                "SPEND_NETWORK_EMAIL is not set - this source signs in with the account "
                "email and password used at https://api.spendnetwork.cloud/docs, not an API key."
            )
        if not self.settings.spend_network_password:
            return (
                "SPEND_NETWORK_PASSWORD is not set - paste the account password here; it is "
                "stored write-only and exchanged for a short-lived bearer token on each sweep."
            )
        return None

    # -- search -------------------------------------------------------------
    @staticmethod
    def build_query(phrases: tuple[str, ...] = SEARCH_PHRASES) -> str:
        """Every phrase quoted, joined by OR.

        The quoting is load-bearing and the reason this is a named function with
        a test of its own: dropping it turns a precise search into the whole
        feed, and the API reports no error when that happens.
        """
        return " OR ".join(f'"{phrase}"' for phrase in phrases if phrase.strip())

    # -- auth ---------------------------------------------------------------
    def _cache_key(self) -> str:
        blob = f"{self.settings.spend_network_email}\x00{self.settings.spend_network_password}"
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    async def token(self, client: httpx.AsyncClient) -> str:
        """A cached bearer token, signing in only when there is not a live one."""
        key = self._cache_key()
        async with _TOKEN_LOCK:
            cached = _TOKEN_CACHE.get(key)
            now = datetime.now(UTC).replace(tzinfo=None)
            if cached and cached[1] - TOKEN_EXPIRY_MARGIN > now:
                return cached[0]
            payload = await self.request(
                client,
                "POST",
                LOGIN_URL,
                expect="json",
                data={
                    "username": self.settings.spend_network_email,
                    "password": self.settings.spend_network_password,
                    "grant_type": "password",
                },
            )
            token = (payload or {}).get("access_token")
            if not token:
                # Never echo the payload: a failed login response is the one
                # place a credential could plausibly be reflected back.
                raise ConnectorError(
                    self.source_name,
                    "sign-in returned no access_token - check SPEND_NETWORK_EMAIL and "
                    "SPEND_NETWORK_PASSWORD",
                    url=LOGIN_URL,
                )
            expires, _ = parse_datetime((payload or {}).get("expiration_date"))
            # A token with no stated expiry is treated as good for an hour, not
            # forever: guessing long and being wrong fails every sweep after it.
            _TOKEN_CACHE[key] = (str(token), expires or (now + timedelta(hours=1)))
            log_ctx(logger, logging.INFO, "signed in", source=self.source_name, expires=str(expires))
            return str(token)

    # -- fetch --------------------------------------------------------------
    async def fetch(self, date_from: datetime, date_to: datetime) -> list[NormalizedTender]:
        date_from, date_to = self.clamp_window(date_from, date_to)
        # The widened floor. See "why the window is widened" above.
        backfill = max(0, self.settings.spend_network_backfill_days)
        window_from = (date_from - timedelta(days=backfill)).date()
        window_to = date_to.date()
        query = self.build_query()

        out: list[NormalizedTender] = []
        seen: set[str] = set()
        received = 0
        skipped = 0
        truncated = False
        total: int | None = None

        async with self.client() as client:
            bearer = await self.token(client)
            headers = {"Authorization": f"Bearer {bearer}"}
            budget = max(1, self.settings.spend_network_max_pages)
            page_size = min(self.settings.page_size, MAX_PAGE_SIZE)

            for page in range(budget):
                offset = page * page_size
                if offset > MAX_OFFSET:
                    truncated = True
                    break
                if page:
                    await self._sleep(self.settings.spend_network_page_pause_seconds)
                params = {
                    "search_term__is": query,
                    "release_date__gte": window_from.isoformat(),
                    "release_date__lte": window_to.isoformat(),
                    "limit": page_size,
                    "offset": offset,
                    # Newest first, so a truncated sweep keeps the recent end.
                    "date_direction": "desc",
                }
                data = await self._page(client, headers, params)
                if data is None:  # throttled, and already waited once
                    truncated = True
                    break
                records = data.get("results") or []
                total = data.get("result_count") if total is None else total
                received += len(records)
                for raw in records:
                    try:
                        tender = self._normalize(raw)
                    except Exception:  # one malformed record must not lose the page
                        skipped += 1
                        continue
                    if tender is None:
                        skipped += 1
                        continue
                    if tender.source_notice_id in seen:
                        continue
                    seen.add(tender.source_notice_id)
                    out.append(tender)
                if len(records) < page_size:
                    break
                if page + 1 >= budget and isinstance(total, int) and total > received:
                    # Never let a capped sweep read as full coverage.
                    truncated = True

        self.log_progress(
            window_from=window_from.isoformat(),
            window_to=window_to.isoformat(),
            matched=total,
            received=received,
            kept=len(out),
            skipped=skipped,
            truncated=truncated,
        )
        return out

    async def _page(
        self, client: httpx.AsyncClient, headers: dict[str, str], params: dict[str, Any]
    ) -> dict[str, Any] | None:
        """One page, with the throttle answered exactly once.

        403 here is not "forbidden", it is "you have spent your requests" - a
        bare HTML page from the front door with nothing to read. It is also the
        one status that must not be retried in a loop, because requests made
        while blocked extend the block. So: one wait, one retry, then give up
        and let the caller report a short sweep.
        """
        try:
            return await self.request(client, "GET", RECORDS_URL, params=params, headers=headers)
        except ConnectorError as exc:
            if exc.status != 403:
                raise
            wait = max(0.0, self.settings.spend_network_throttle_backoff_seconds)
            log_ctx(
                logger,
                logging.WARNING,
                "throttled, waiting once",
                source=self.source_name,
                wait=round(wait, 1),
            )
            await self._sleep(wait)
        try:
            return await self.request(client, "GET", RECORDS_URL, params=params, headers=headers)
        except ConnectorError as exc:
            if exc.status != 403:
                raise
            log_ctx(logger, logging.WARNING, "still throttled, stopping", source=self.source_name)
            return None

    # -- normalization ------------------------------------------------------
    def _normalize(self, raw: dict[str, Any]) -> NormalizedTender | None:
        ocid = raw.get("ocid")
        if not ocid:
            # The OCDS id is the only field unique across forty upstream
            # portals; tender_id collides between them.
            return None

        published, published_tz = parse_datetime(raw.get("release_date"))
        # closing_date and tender_end_date agreed on every record sampled where
        # both were present; closing_date is populated slightly more often.
        deadline, deadline_tz = parse_datetime(raw.get("closing_date") or raw.get("tender_end_date"))
        updated, _ = parse_datetime(raw.get("date_updated") or raw.get("date_created"))

        title = _text(raw.get("tender_title"))
        description = _text(raw.get("tender_description")) or _text(raw.get("content"))
        release_tags = _text(raw.get("release_tags"))
        value, currency = _value(raw)

        return NormalizedTender(
            source=self.source_name,
            source_notice_id=str(ocid),
            source_url=_text(raw.get("tender_url")),
            # The upstream portal's own reference, which is what a buyer quotes.
            reference_number=_text(raw.get("notice_id")) or _text(raw.get("tender_id")),
            # 4 of 97 records carried no title. The ocid is a poor label but it
            # is a true one, and a blank row cannot be looked up later.
            title=title or str(ocid),
            description=description,
            buyer_name=_text(raw.get("buyer_name")),
            buyer_country=_text(raw.get("buyer_address_iso_alpha2")),
            delivery_location=_location(raw),
            publication_date=published,
            deadline=deadline,
            # What "new to us" means for an aggregator is when *it* saw the
            # notice, which is also what an incremental sweep should compare.
            source_updated_at=updated or published,
            # The source's own label ("UTC+02:00"), kept verbatim beside the UTC
            # value rather than reinterpreted.
            source_timezone=_text(raw.get("source_timezone")) or deadline_tz or published_tz,
            status=_text(raw.get("tag_status")) or status_from_deadline(deadline),
            procurement_stage=stage_from_code(release_tags),
            notice_type=release_tags,
            estimated_value=value,
            currency=currency,
            classification_codes=_codes(raw),
            # tender_url is the notice page, not a document, and it is already
            # source_url. There is a separate attachments endpoint; it costs a
            # request per notice against a metered account, so it is not called.
            document_urls=[],
            language=_text(raw.get("language")),
            raw_payload=raw,
        )


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _location(raw: dict[str, Any]) -> str | None:
    """Locality, region and country, as much of it as the upstream portal gave."""
    parts = [
        _text(raw.get("buyer_address_locality")),
        _text(raw.get("buyer_address_region")),
        _text(raw.get("buyer_address_country_name")),
    ]
    seen: list[str] = []
    for part in parts:
        if part and part not in seen:
            seen.append(part)
    return ", ".join(seen) or None


def _value(raw: dict[str, Any]) -> tuple[float | None, str | None]:
    """The tender value in its own currency, falling back to a converted one.

    Absent values arrive as 0.0 rather than null, so zero means "not stated" -
    and 48 of 97 records carried a currency while only 35 carried an amount, so
    neither field implies the other. The converted GBP/EUR/USD trio is Spend
    Network's own arithmetic; it is used only when the native amount is missing,
    and the currency stored then says which one was taken.
    """
    amount = _number(raw.get("tender_amount"))
    currency = _text(raw.get("tender_currency"))
    if amount is not None:
        return amount, currency
    for field, code in (
        ("tender_gbp_value", "GBP"),
        ("tender_eur_value", "EUR"),
        ("tender_usd_value", "USD"),
    ):
        converted = _number(raw.get(field))
        if converted is not None:
            return converted, code
    return None, currency


def _number(value: Any) -> float | None:
    if value in (None, "", "null"):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number or None


def _codes(raw: dict[str, Any]) -> list[dict[str, Any]]:
    """CPV and NAICS, with Spend Network's *predicted* CPV kept apart.

    `cpv_codes` is what the buyer published and was present on 20 of 97 records.
    `cpv_aug_data` is a model's guess, present on all 97 and carrying its own
    relevance score - useful, but filed under its own scheme so nothing
    downstream can mistake a prediction for a classification the buyer made.
    """
    out: list[dict[str, Any]] = []
    names = raw.get("cpv_names") or []
    for index, code in enumerate(raw.get("cpv_codes") or []):
        if not code:
            continue
        entry: dict[str, Any] = {"scheme": "CPV", "code": str(code)}
        if index < len(names) and names[index]:
            entry["description"] = str(names[index])
        out.append(entry)
    for item in raw.get("cpv_aug_data") or []:
        code = (item or {}).get("cpv_aug_codes")
        if not code:
            continue
        entry = {"scheme": "CPV-PREDICTED", "code": str(code)}
        if item.get("cpv_aug_names"):
            entry["description"] = str(item["cpv_aug_names"])
        if item.get("relevance_score") is not None:
            entry["confidence"] = item["relevance_score"]
        out.append(entry)
    for item in raw.get("naics") or []:
        code = (item or {}).get("naics_code")
        if not code:
            continue
        entry = {"scheme": "NAICS", "code": str(code)}
        if item.get("naics_title"):
            entry["description"] = str(item["naics_title"])
        out.append(entry)
    return out
