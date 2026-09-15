"""Spend Network - Open Opportunities: one login over ~40 national portals.

    POST https://api.spendnetwork.cloud/api/v3/login/access-token   (email + password)
    GET  https://api.spendnetwork.cloud/api/v3/notices/records_openopps

Requires SPEND_NETWORK_EMAIL *and* SPEND_NETWORK_PASSWORD. Unlike every other
source here the credential is an account, not a key: there is no API key to
mint, so the connector signs in and carries a bearer token.

WHAT THIS ADDS THAT THE OTHER TEN DO NOT
    It is an aggregator, and a wide one. A single 31-day window returned notices
    from fifteen upstream portals across fourteen countries - German DTVP and
    oeffentlichevergabe, French BOAMP, achatpublic and marchesonline, Irish and
    Northern Irish eTenders, Canadian bidsandtenders, AusTender, PhilGEPS, US
    opengov - most of which this system has no connector for and several of
    which publish no usable feed at all. Every record is OCDS-shaped and carries
    an `ocid`, so the overlap with TED and Find a Tender deduplicates on content
    rather than accumulating.

THE FEED IS TAKEN WHOLE, AND FILTERED HERE
    This connector used to ask the API to search for us, with the repo's phrase
    list quoted and ORed into `search_term__is`. It no longer does, by decision:
    the filter is ours, not the vendor's, and a phrase list that lives in our
    code can be widened tomorrow without asking anyone.

    The cost of that is the whole firehose. Measured over 24 consecutive days
    the feed ran 735-6,239 notices a day, mean 3,995 - against 108 for a whole
    month through the old keyword query. So every notice is now paged and
    `keep()` decides what is stored, on this repo's own PREFILTER_TERMS, exactly
    as the UK feeds, CanadaBuys, AusTender and PNCP already do.

    Two consequences worth stating plainly. Widening the term list later does
    not recover the past - what was not kept was never stored, only re-fetchable
    - and `APPLY_KEYWORD_PREFILTER=false` would mean storing ~1.46M notices a
    year here. See `keep()` below for why that switch alone cannot do it.

THE DAY IS THE CHUNK, BECAUSE PAGING STOPS AT 10,000
    `offset` refuses anything over 9,900 and `result_count` saturates at 10,000,
    so no single query can reach past ten thousand records however it is paged.
    A day never came close in 24 days of measurement (the busiest was 6,239), so
    the window is walked one day at a time and each day is paged to exhaustion.
    A window is therefore N days of roughly 40 requests each, not one query.

THE QUOTA IS THE BUDGET, AND IT IS NOT A RATE LIMIT
    The account meters *requests*, and exhaustion is not an API error: it is a
    bare nginx `403 Forbidden` HTML page with no Retry-After, no rate-limit
    headers and no JSON.

    It is a fixed budget rather than a rate. Twenty-five requests tripped it
    flat-out, and twenty-five tripped it again when they were paced three
    seconds apart over ninety-three seconds - the same count both times, so
    slowing down buys nothing. Recovery takes about five minutes of *silence*:
    still refused at 150s, clear at 302s, after which the budget refilled to 35
    requests. Roughly thirty requests per five minutes, so a mean day of feed
    (40 requests) costs about seven minutes and the worst measured day about
    eleven.

    And the part that decides the shape of the code below: *every request made
    while blocked extends the block*. Polling once every twenty seconds kept it
    shut for four minutes. So a 403 is never polled - it is waited out in
    silence, once per occurrence, and the page it interrupted is retried at the
    same offset rather than skipped. A sweep that exhausts its wait budget stops
    and reports what it has, because guessing is worse than a short sweep that
    says it was short.

WHY A CHEAP KEYWORD PASS SURVIVES AT THE END
    The only date filter is `release_date` - when the *upstream portal*
    published, not when Spend Network ingested. A notice released weeks ago can
    be aggregated today, and a sweep windowed on release_date would never see
    it. Paging the whole feed back thirty days to catch those would cost about
    four hours.

    So the backfill keeps the old trick and pays two requests for it: one
    keyword-filtered query, phrases quoted and ORed, over the wider window. It
    catches only late arrivals that match the *phrase* list rather than the
    broader prefilter, which is a real gap and is logged as one - but two
    requests for most of the value is the right trade against fourteen hundred.

    `search_term__is` is a real search (`zzzzznonsensequery` returns 0) but ORs
    bare words: unquoted, `safety data sheet` matched 4,770 notices in a
    fortnight and quoted it matched 23. So that pass quotes every phrase, and
    `build_query` keeps its own test. Unknown query parameters are accepted and
    silently ignored, so only parameters in the published OpenAPI document are
    sent and none is trusted to have filtered without being measured.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
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
from app.connectors.keywords import SEARCH_PHRASES, looks_relevant
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


@dataclass
class _Sweep:
    """One sweep's running totals, and the two budgets that bound it.

    Both budgets exist because this source is the only one whose cost is a
    function of the *window* rather than of how much matched. A month asked for
    by hand is 1,200 requests and four hours; nothing should be able to start
    that by accident, and nothing should report it as full coverage if it does.
    """

    budget: int
    waits: int
    requests: int = 0
    days: int = 0
    seen: int = 0
    filtered: int = 0
    skipped: int = 0
    backfilled: int = 0
    throttled: int = 0
    truncated: bool = False
    #: Set when the account is blocked and there is no patience left. The block
    #: is account-wide, not per-day, so walking on to the next day would spend
    #: the remaining budget collecting 403s.
    stop: bool = False

    def spent(self) -> bool:
        return self.stop or self.requests >= self.budget


def _days(first: date, last: date) -> Iterator[date]:
    """Every day in the window, newest first.

    Newest first so that a sweep which runs out of budget has covered the recent
    end, which is the end anybody is waiting on.
    """
    day = last
    while day >= first:
        yield day
        day -= timedelta(days=1)


class SpendNetworkConnector(TenderConnector):
    source_name = "spend_network"
    display_name = "Spend Network (Open Opportunities)"
    homepage = "https://www.spendnetwork.com"
    requires_api_key = True
    credential_label = "password"
    # The whole feed is paged and filtered here rather than searched server-side,
    # so this is the filter - not a second, coarser copy of one. Same mechanism
    # the UK feeds, CanadaBuys, AusTender and PNCP use.
    prefilter = True
    notes = (
        "An aggregator: one account covers ~40 national portals (German DTVP, French BOAMP, "
        "Irish eTenders, PhilGEPS, AusTender and more) that have no connector of their own. "
        "Needs an email and a password rather than an API key. The whole feed is paged - "
        "735-6,239 notices a day, mean 3,995 - and filtered here on our own term list, one "
        "day at a time because paging stops dead at 10,000 records. A measured day kept 78 "
        "notices out of 1,919. The account meters requests (~30 per 5 minutes) and answers a "
        "bare HTML 403 when they run out, so a day of feed costs about seven minutes and a "
        "refusal is waited out in silence, never polled: every request made while blocked "
        "extends the block."
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
    def keep_record(self, raw: dict[str, Any]) -> bool:
        """Our own term list, applied to the title, the buyer and the description.

        Which fields is not a matter of taste, and the first answer was wrong,
        so both measurements are recorded here.

        Against a raw day of the feed - 1,919 notices, 2 of which scored 50 or
        better::

            title + buyer                17 kept (0.9%)
            + description                78 kept (4.1%)
            + content                    78 kept - identical, not one extra row

        So `content` earns nothing and is left out: it is the whole notice body,
        the field most likely to be boilerplate, and on unbiased data it changed
        nothing at all. An earlier run said it recovered a notice, but that run
        was over 108 records that had *arrived through the vendor's own
        full-text search of content* - they were selected for having their
        signal in the body, which is exactly the bias that makes the number
        meaningless.

        `description` stays, and is the expensive half of the choice: 78 rows a
        day against 17. It stays because that same biased sample - the only
        evidence available about notices whose subject is not in the title -
        had title-only losing ten of twelve notices scoring 50 or better, and
        these buyers do put a procurement reference in the title and the subject
        in the description. Sixty extra rows a day is about 1.5GB a year. A
        missed tender costs a bid. The asymmetry decides it.
        """
        return self.keep(
            _text(raw.get("tender_title")),
            _text(raw.get("buyer_name")),
            _text(raw.get("tender_description")),
        )

    def keep(self, *texts: str | None) -> bool:
        """The prefilter here is not optional, and APPLY_KEYWORD_PREFILTER cannot turn it off.

        That switch means "store the whole window rather than the topical part
        of it", which is a reasonable thing to want of a national feed. Of a
        global aggregator it means 1.46 million notices a year at ~25KB each -
        roughly 37GB - arriving because somebody flipped a setting whose name
        says nothing about this source. So the escape hatch is its own setting,
        deliberately named, and the general switch does not reach it.
        """
        if self.settings.spend_network_store_unfiltered:
            return True
        return looks_relevant(*texts)

    async def fetch(self, date_from: datetime, date_to: datetime) -> list[NormalizedTender]:
        date_from, date_to = self.clamp_window(date_from, date_to)
        state = _Sweep(
            budget=max(1, self.settings.spend_network_max_requests_per_sweep),
            waits=max(0, self.settings.spend_network_max_throttle_waits),
        )
        out: list[NormalizedTender] = []
        seen: set[str] = set()

        async with self.client() as client:
            bearer = await self.token(client)
            headers = {"Authorization": f"Bearer {bearer}"}

            # One day at a time: paging stops dead at 10,000 records and a day
            # has never come near that, while a multi-day window would.
            for day in _days(date_from.date(), date_to.date()):
                if state.spent():
                    break
                await self._collect_day(client, headers, day, state, out, seen)

            # The late-arrival net. Two requests for the notices Spend Network
            # aggregated inside the window but whose *upstream* publication date
            # falls outside it - see the module docstring.
            await self._collect_backfill(client, headers, date_from, state, out, seen)

        self.log_progress(
            window_from=date_from.date().isoformat(),
            window_to=date_to.date().isoformat(),
            days=state.days,
            requests=state.requests,
            seen=state.seen,
            kept=len(out),
            dropped_by_filter=state.filtered,
            skipped=state.skipped,
            throttle_waits=state.throttled,
            truncated=state.truncated,
        )
        return out

    async def _collect_day(
        self,
        client: httpx.AsyncClient,
        headers: dict[str, str],
        day: date,
        state: _Sweep,
        out: list[NormalizedTender],
        seen: set[str],
    ) -> None:
        """Page one day to exhaustion, or until the sweep runs out of budget."""
        state.days += 1
        offset = 0
        page_size = min(self.settings.page_size, MAX_PAGE_SIZE)
        while offset <= MAX_OFFSET:
            if state.spent():
                state.truncated = True
                return
            if state.requests:
                await self._sleep(self.settings.spend_network_page_pause_seconds)
            data = await self._page(
                client,
                headers,
                {
                    "release_date__gte": day.isoformat(),
                    "release_date__lte": day.isoformat(),
                    "limit": page_size,
                    "offset": offset,
                    # Newest first, so a sweep cut short keeps the recent end.
                    "date_direction": "desc",
                },
                state,
            )
            if data is None:
                state.truncated = True
                return
            records = data.get("results") or []
            state.seen += len(records)
            self._absorb(records, state, out, seen)
            if len(records) < page_size:
                return
            offset += page_size
        # Only reachable if a single day ever exceeds the paging ceiling, which
        # 24 days of measurement never saw. Said out loud rather than assumed
        # away: it would be silent under-coverage of the busiest day.
        state.truncated = True
        log_ctx(
            logger,
            logging.WARNING,
            "day exceeded the paging ceiling",
            source=self.source_name,
            day=day.isoformat(),
            ceiling=MAX_OFFSET + MAX_PAGE_SIZE,
        )

    async def _collect_backfill(
        self,
        client: httpx.AsyncClient,
        headers: dict[str, str],
        date_from: datetime,
        state: _Sweep,
        out: list[NormalizedTender],
        seen: set[str],
    ) -> None:
        """The cheap keyword pass over the wider window. See the module docstring."""
        days = max(0, self.settings.spend_network_backfill_days)
        if not days or state.spent():
            return
        floor = (date_from - timedelta(days=days)).date()
        ceiling = (date_from - timedelta(days=1)).date()
        if floor > ceiling:
            return
        page_size = min(self.settings.page_size, MAX_PAGE_SIZE)
        offset = 0
        while offset <= MAX_OFFSET and not state.spent():
            await self._sleep(self.settings.spend_network_page_pause_seconds)
            data = await self._page(
                client,
                headers,
                {
                    "search_term__is": self.build_query(),
                    "release_date__gte": floor.isoformat(),
                    "release_date__lte": ceiling.isoformat(),
                    "limit": page_size,
                    "offset": offset,
                    "date_direction": "desc",
                },
                state,
            )
            if data is None:
                state.truncated = True
                return
            records = data.get("results") or []
            state.seen += len(records)
            state.backfilled += self._absorb(records, state, out, seen)
            if len(records) < page_size:
                return
            offset += page_size

    def _absorb(
        self,
        records: list[dict[str, Any]],
        state: _Sweep,
        out: list[NormalizedTender],
        seen: set[str],
    ) -> int:
        """Filter, normalize and collect one page. Returns how many were kept."""
        kept = 0
        for raw in records:
            if not self.keep_record(raw):
                state.filtered += 1
                continue
            try:
                tender = self._normalize(raw)
            except Exception:  # one malformed record must not lose the page
                state.skipped += 1
                continue
            if tender is None:
                state.skipped += 1
                continue
            if tender.source_notice_id in seen:
                continue
            seen.add(tender.source_notice_id)
            out.append(tender)
            kept += 1
        return kept

    async def _page(
        self,
        client: httpx.AsyncClient,
        headers: dict[str, str],
        params: dict[str, Any],
        state: _Sweep,
    ) -> dict[str, Any] | None:
        """One page, with the throttle waited out rather than treated as failure.

        403 here is not "forbidden", it is "you have spent your requests" - a
        bare HTML page from the front door with nothing to read. When the whole
        feed is being paged, hitting it is not an exception: a mean day costs
        forty requests against a budget of about thirty per five minutes, so
        every day of feed trips it once or twice *by design*. Treating that as a
        failed sweep would mean never finishing a single day.

        So the wait is the mechanism, not the error path. What must not happen is
        polling: every request made while blocked extends the block, which is why
        this sleeps the full measured recovery in silence and then retries the
        *same* offset rather than moving on - a skipped page is a hole in the
        window that nothing downstream could ever notice.

        Returns None only when the sweep has run out of patience, which the
        caller reports as truncated.
        """
        while True:
            if state.spent():
                state.truncated = True
                return None
            state.requests += 1
            try:
                return await self.request(client, "GET", RECORDS_URL, params=params, headers=headers)
            except ConnectorError as exc:
                if exc.status != 403:
                    raise
            if state.throttled >= state.waits:
                log_ctx(
                    logger,
                    logging.WARNING,
                    "out of throttle waits, stopping",
                    source=self.source_name,
                    waits=state.throttled,
                    requests=state.requests,
                )
                state.truncated = True
                state.stop = True
                return None
            state.throttled += 1
            wait = max(0.0, self.settings.spend_network_throttle_backoff_seconds)
            log_ctx(
                logger,
                logging.INFO,
                "request budget spent, waiting it out",
                source=self.source_name,
                wait=round(wait, 1),
                wait_number=state.throttled,
                requests=state.requests,
            )
            await self._sleep(wait)

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
