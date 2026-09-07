"""Connector tests. Every HTTP call is served from a saved fixture.

The JSON/CSV/XML fixtures under tests/fixtures were captured from the live
public APIs (SAM.gov, which needs a personal key, is modelled on its published
v2 response schema).
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

import httpx
import pytest

from app.connectors.austender import AusTenderConnector
from app.connectors.base import ConnectorError, NormalizedTender, TenderConnector, parse_datetime
from app.connectors.canada_buys import CanadaBuysConnector
from app.connectors.contracts_finder import ContractsFinderConnector
from app.connectors.find_a_tender import FindATenderConnector
from app.connectors.highergov import HigherGovConnector
from app.connectors.pncp import PncpConnector
from app.connectors.registry import SOURCE_NAMES, build_all, build_connector, source_catalog
from app.connectors.sam import SamGovConnector
from app.connectors.ted import TedConnector
from app.connectors.world_bank import WorldBankConnector
from tests.conftest import fixture_json, fixture_text

DATE_FROM = datetime(2026, 8, 18)
DATE_TO = datetime(2026, 8, 21)


def transport(handler) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


async def fetch(connector: TenderConnector) -> list[NormalizedTender]:
    return await connector.fetch(DATE_FROM, DATE_TO)


# --- TED -------------------------------------------------------------------


async def test_ted_iteration_pagination_and_normalization(settings):
    calls: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = request.read().decode()
        calls.append({"url": str(request.url), "body": payload})
        page = "ted_page2.json" if "TOKEN-PAGE-2" in payload else "ted_page1.json"
        return httpx.Response(200, json=fixture_json(page), headers={"content-type": "application/json"})

    tenders = await fetch(TedConnector(settings, transport=transport(handler)))
    assert len(calls) == 2, "iteration token must be followed"
    query = json.loads(calls[0]["body"])["query"]
    assert 'FT ~ "safety data sheet"' in query
    assert "publication-date>=20260818" in query
    assert "publication-date<=20260821" in query
    assert len(tenders) == 2
    first = tenders[0]
    assert first.source == "ted"
    assert first.source_notice_id
    assert first.source_url.startswith("https://ted.europa.eu/en/notice/-/detail/")
    assert first.title
    assert first.buyer_country
    assert first.procurement_stage in {"planning", "tender", "award"}
    assert all(c["scheme"] == "CPV" for c in first.classification_codes)
    assert first.raw_payload, "the raw source record must be preserved"
    assert first.content_hash


async def test_ted_skips_malformed_records_without_dropping_the_page(settings):
    good = fixture_json("ted_page1.json")["notices"][0]

    def handler(request: httpx.Request) -> httpx.Response:
        body = {
            "notices": [
                {"notice-title": {"eng": "no publication number"}},  # unusable
                {"publication-number": ["broken", {"unexpected": "shape"}], "notice-title": 12345},
                good,
            ],
            "totalNoticeCount": 3,
            "iterationNextToken": None,
        }
        return httpx.Response(200, json=body, headers={"content-type": "application/json"})

    tenders = await fetch(TedConnector(settings, transport=transport(handler)))
    assert len(tenders) >= 1
    assert all(t.source_notice_id for t in tenders)


async def test_ted_rejects_wrong_content_type(settings):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>maintenance</html>", headers={"content-type": "text/html"})

    with pytest.raises(ConnectorError) as exc:
        await fetch(TedConnector(settings, transport=transport(handler)))
    assert "content-type" in str(exc.value)


# --- rate limiting / retries ----------------------------------------------


async def test_http_429_is_retried_and_respects_retry_after(settings):
    attempts: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        if len(attempts) == 1:
            return httpx.Response(429, json={"error": "slow down"}, headers={"Retry-After": "0"})
        return httpx.Response(200, json=fixture_json("ted_page2.json"))

    connector = TedConnector(settings, transport=transport(handler))
    tenders = await fetch(connector)
    assert len(attempts) == 2
    assert len(tenders) == 1


async def test_retry_after_header_parsing(settings):
    connector = TedConnector(settings)
    seconds = connector._retry_after(httpx.Response(429, headers={"Retry-After": "42"}))
    assert seconds == 42.0
    http_date = connector._retry_after(
        httpx.Response(429, headers={"Retry-After": "Wed, 21 Oct 2015 07:28:00 GMT"})
    )
    assert http_date == 0.0  # date in the past clamps to zero
    assert connector._retry_after(httpx.Response(429)) is None


async def test_server_error_gives_up_after_max_retries(settings):
    attempts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        return httpx.Response(503, json={"error": "unavailable"})

    with pytest.raises(ConnectorError) as exc:
        await fetch(TedConnector(settings, transport=transport(handler)))
    assert exc.value.retryable is True
    assert len(attempts) == settings.max_retries + 1


async def test_client_error_is_not_retried(settings):
    attempts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        return httpx.Response(400, json={"message": "bad query"})

    with pytest.raises(ConnectorError) as exc:
        await fetch(TedConnector(settings, transport=transport(handler)))
    assert len(attempts) == 1
    assert exc.value.status == 400
    assert exc.value.retryable is False


async def test_oversized_response_is_rejected(settings):
    small = settings.model_copy(update={"max_response_bytes": 50})

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=fixture_json("ted_page1.json"))

    with pytest.raises(ConnectorError) as exc:
        await fetch(TedConnector(small, transport=transport(handler)))
    assert "too large" in str(exc.value)


# --- SAM.gov ---------------------------------------------------------------

SAM_PAGE = {
    "totalRecords": 2,
    "limit": 1,
    "offset": 0,
    "opportunitiesData": [
        {
            "noticeId": "abc123",
            "title": "Chemical inventory and safety data sheet management software",
            "solicitationNumber": "W91QVN-26-R-0042",
            "fullParentPathName": "DEPT OF DEFENSE.DEPT OF THE ARMY",
            "postedDate": "2026-08-19",
            "type": "Solicitation",
            "baseType": "Presolicitation",
            "active": "Yes",
            "responseDeadLine": "2026-09-15T17:00:00-04:00",
            "naicsCode": "541511",
            "naicsCodes": [{"code": ["541512"]}],
            "classificationCode": "7A20",
            "description": "https://api.sam.gov/prod/opportunities/v1/noticedesc?noticeid=abc123",
            "uiLink": "https://sam.gov/opp/abc123/view",
            "resourceLinks": ["https://sam.gov/api/prod/opps/v3/opportunities/resources/files/x/download"],
            "placeOfPerformance": {
                "city": {"name": "Aberdeen"},
                "state": {"name": "Maryland"},
                "country": {"name": "UNITED STATES"},
            },
        }
    ],
}
SAM_EMPTY = {"totalRecords": 2, "limit": 1, "offset": 1, "opportunitiesData": []}


async def test_sam_is_disabled_without_api_key(settings):
    """Only on the API path. The bulk extract needs no credential - see below."""
    settings = settings.model_copy(update={"sam_use_bulk_extract": False})
    connector = SamGovConnector(settings)
    assert connector.requires_api_key is True
    assert connector.unavailable_reason() is not None
    assert "SAM_GOV_API_KEY" in connector.unavailable_reason()


async def test_sam_bulk_extract_needs_no_key_and_is_the_default(settings):
    """The free daily extract, which is how SAM works at all on a role-less key.

    The metered API allows 10 requests a day, which one sweep used to exhaust.
    This file is keyless, unmetered, and carries the description inline - so it
    is the default, and a missing SAM_GOV_API_KEY no longer disables the source.
    """
    connector = SamGovConnector(settings)
    assert connector.requires_api_key is False
    assert connector.unavailable_reason() is None, "no key is needed for the extract"


async def test_sam_bulk_extract_filters_type_window_and_topic(settings):
    """One request, one file, and only the rows that belong in the window.

    The fixture carries the live extract's real 47-column header and four rows:
    one keeper, one right-topic-wrong-notice-type (the API's ptype filter), one
    right-topic-outside-the-window, and one in-window irrelevant row.
    """
    urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        urls.append(str(request.url))
        return httpx.Response(200, content=fixture_text("sam_extract.csv").encode())

    tenders = await fetch(SamGovConnector(settings, transport=transport(handler)))

    assert len(urls) == 1, "the whole sweep is one download"
    assert "api.sam.gov" not in urls[0], "the metered API must not be touched"
    assert [t.source_notice_id for t in tenders] == ["aa11bb22cc33dd44ee55ff6677889900"]

    tender = tenders[0]
    assert tender.source == "sam"
    assert tender.reference_number == "W91QVN-26-R-0042"
    # Three columns rebuilt into the dotted path the API used to return whole.
    assert tender.buyer_name == "DEPT OF DEFENSE.DEPT OF THE ARMY.W07V ENDIST NEW ORLEANS"
    assert tender.buyer_country == "US"
    assert tender.deadline == datetime(2026, 9, 15, 21, 0)
    assert tender.status == "open"
    assert {"scheme": "NAICS", "code": "541511"} in tender.classification_codes
    assert {"scheme": "PSC", "code": "7A20"} in tender.classification_codes
    # The description arrives inline - the whole reason this transport is better
    # than the API, which charged a second request per notice for it.
    assert "cloud based SDS management system" in tender.description
    assert "chemical inventory tracking" in tender.description, "quoted newlines survived"


async def test_sam_bulk_extract_refuses_to_grow_without_bound(settings):
    """A 242 MB download needs a ceiling, not trust."""
    settings = settings.model_copy(update={"sam_extract_max_bytes": 32})

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=fixture_text("sam_extract.csv").encode())

    with pytest.raises(ConnectorError, match="exceeded"):
        await fetch(SamGovConnector(settings, transport=transport(handler)))


def _sam_handler(urls: list[str]):
    def handler(request: httpx.Request) -> httpx.Response:
        urls.append(str(request.url))
        if "noticedesc" in str(request.url):
            return httpx.Response(200, json={"description": "<p>Cloud based SDS platform</p>"})
        offset = request.url.params.get("offset")
        body = SAM_PAGE if offset == "0" else SAM_EMPTY
        return httpx.Response(200, json=body)

    return handler


async def test_sam_default_budget_is_one_request(keyed_settings):
    """SAM.gov allows 10 requests a *day* on a role-less account.

    The connector used to spend up to 80 in one sweep - 20 pages plus 60
    description fetches - which exhausted the quota on the first sweep of the
    day and made every later request 429 until the 00:00 UTC reset. One sweep
    must cost one request, and the description must be left alone rather than
    fetched.
    """
    urls: list[str] = []
    keyed_settings = keyed_settings.model_copy(update={"sam_use_bulk_extract": False})
    tenders = await fetch(SamGovConnector(keyed_settings, transport=transport(_sam_handler(urls))))

    assert len(urls) == 1, f"one sweep must cost one request, spent {len(urls)}"
    assert "noticedesc" not in urls[0]
    # Unfetched, so the raw link survives as the description rather than a lie.
    assert tenders[0].description.startswith("http")


async def test_sam_pagination_description_fetch_and_key_never_leaks(keyed_settings):
    urls: list[str] = []
    keyed_settings = keyed_settings.model_copy(
        update={
            "sam_use_bulk_extract": False,
            "sam_max_pages": 5,
            "sam_max_description_fetches": 60,
        }
    )

    tenders = await fetch(SamGovConnector(keyed_settings, transport=transport(_sam_handler(urls))))
    assert len(tenders) == 1
    tender = tenders[0]
    assert tender.source == "sam"
    assert tender.reference_number == "W91QVN-26-R-0042"
    assert tender.description == "Cloud based SDS platform"
    assert {"scheme": "NAICS", "code": "541511"} in tender.classification_codes
    assert {"scheme": "PSC", "code": "7A20"} in tender.classification_codes
    assert tender.buyer_country == "US"
    assert tender.deadline == datetime(2026, 9, 15, 21, 0)
    assert tender.status == "open"
    assert "postedFrom=08%2F18%2F2026" in urls[0]
    # The key travels in the query string; nothing that is stored may contain it.
    assert "test-key-not-real" not in str(tender.raw_payload)
    error = ConnectorError("sam", "boom", url=urls[0])
    assert "test-key-not-real" not in str(error)
    assert "api_key=***" in error.to_dict()["url"]


# --- UK OCDS sources -------------------------------------------------------


async def test_find_a_tender_follows_cursor_and_normalizes_ocds(settings):
    urls: list[str] = []
    relaxed = settings.model_copy(update={"apply_keyword_prefilter": False})

    def handler(request: httpx.Request) -> httpx.Response:
        urls.append(str(request.url))
        page = (
            "find_a_tender_page2.json" if "cursor=PAGE2" in str(request.url) else "find_a_tender_page1.json"
        )
        return httpx.Response(200, json=fixture_json(page))

    tenders = await fetch(FindATenderConnector(relaxed, transport=transport(handler)))
    assert len(urls) == 2, "links.next cursor must be followed"
    assert "updatedFrom=2026-08-18T00%3A00%3A00" in urls[0]
    assert len(tenders) == 2
    assert {t.procurement_stage for t in tenders} <= {"planning", "tender", "award"}
    assert all(t.source == "find_a_tender" for t in tenders)
    assert all(t.buyer_country for t in tenders)
    assert all(t.source_url for t in tenders)


async def test_find_a_tender_prefilter_keeps_only_topical_notices(settings):
    def handler(request: httpx.Request) -> httpx.Response:
        body = fixture_json("find_a_tender_page1.json")
        body["links"] = {}
        body["releases"] = [
            {
                "id": "999-2026",
                "ocid": "ocds-h6vhtk-999",
                "tag": ["tender"],
                "date": "2026-08-19T10:00:00+01:00",
                "buyer": {"name": "Test Council"},
                "tender": {
                    "title": "Cloud EHS incident management platform",
                    "description": "SaaS platform for incident management and safety data sheets.",
                    "tenderPeriod": {"endDate": "2026-09-30T12:00:00+01:00"},
                    "value": {"amount": 250000, "currency": "GBP"},
                    "items": [{"classification": {"scheme": "CPV", "id": "48000000"}}],
                    "documents": [{"url": "https://example.org/notice", "documentType": "tenderNotice"}],
                },
            },
            {
                "id": "998-2026",
                "ocid": "ocds-h6vhtk-998",
                "tag": ["tender"],
                "date": "2026-08-19T10:00:00+01:00",
                "tender": {"title": "Grass cutting services", "description": "Mowing verges."},
            },
        ]
        return httpx.Response(200, json=body)

    tenders = await fetch(FindATenderConnector(settings, transport=transport(handler)))
    assert [t.source_notice_id for t in tenders] == ["999-2026"]
    tender = tenders[0]
    assert tender.estimated_value == 250000
    assert tender.currency == "GBP"
    assert tender.deadline == datetime(2026, 9, 30, 11, 0)
    assert tender.source_timezone == "+01:00"
    assert tender.classification_codes == [{"scheme": "CPV", "code": "48000000", "description": None}]
    assert tender.source_url == "https://example.org/notice"


async def test_contracts_finder_filters_by_stage_and_dates(settings):
    urls: list[str] = []
    relaxed = settings.model_copy(update={"apply_keyword_prefilter": False})

    def handler(request: httpx.Request) -> httpx.Response:
        urls.append(str(request.url))
        return httpx.Response(200, json=fixture_json("contracts_finder_page1.json"))

    tenders = await fetch(ContractsFinderConnector(relaxed, transport=transport(handler)))
    assert "stages=tender%2Cplanning" in urls[0]
    assert "publishedFrom=2026-08-18T00%3A00%3A00" in urls[0]
    assert tenders
    assert all(t.source == "contracts_finder" for t in tenders)
    assert all(t.reference_number.startswith("ocds-") for t in tenders)
    assert all(t.source_notice_id for t in tenders)


# --- World Bank ------------------------------------------------------------


async def test_world_bank_offset_pagination_and_award_filtering(settings):
    params: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        params.append(dict(request.url.params))
        body = fixture_json("world_bank_page1.json")
        body["procnotices"] = body["procnotices"] + [
            {
                "id": "OP-AWARD",
                "notice_type": "Contract Award",
                "notice_status": "Published",
                "noticedate": "19-Aug-2026",
                "bid_description": "Chemical management award",
                "submission_date": "2026-08-19T00:00:00Z",
            },
            {
                "id": "OP-OPEN",
                "notice_type": "Invitation for Bids",
                "notice_status": "Published",
                "noticedate": "19-Aug-2026",
                "bid_description": "Cloud chemical inventory and safety data sheet platform",
                "submission_deadline_date": "2026-12-01T00:00:00Z",
                "submission_deadline_time": "09:30",
                "project_ctry_name": "Kenya",
                "project_id": "P123456",
                "notice_text": "<p>Cloud <strong>SaaS</strong> platform</p>",
            },
        ]
        return httpx.Response(200, json=body)

    tenders = await fetch(WorldBankConnector(settings, transport=transport(handler)))
    ids = {t.source_notice_id for t in tenders}
    assert "OP-OPEN" in ids
    assert "OP-AWARD" not in ids, "contract awards are not opportunities"
    assert all("qterm" in p for p in params)
    assert {p["os"] for p in params} == {"0"}  # short pages stop the loop
    open_notice = next(t for t in tenders if t.source_notice_id == "OP-OPEN")
    assert open_notice.source_url.endswith("/OP-OPEN")
    assert open_notice.deadline == datetime(2026, 12, 1)
    assert "SaaS platform" in open_notice.description
    assert "<p>" not in open_notice.description


async def test_world_bank_survives_a_failing_keyword_query(settings):
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) <= settings.max_retries + 1:
            return httpx.Response(503, json={"error": "unavailable"})
        body = fixture_json("world_bank_page1.json")
        body["procnotices"] = [
            {
                "id": "OP-LATER-TERM",
                "notice_type": "Invitation for Bids",
                "notice_status": "Published",
                "noticedate": "19-Aug-2026",
                "bid_description": "Cloud safety data sheet management platform",
                "submission_deadline_date": "2099-01-01T00:00:00Z",
            }
        ]
        return httpx.Response(200, json=body)

    tenders = await fetch(WorldBankConnector(settings, transport=transport(handler)))
    assert tenders, "later keywords must still be queried after one fails"


# --- CanadaBuys ------------------------------------------------------------


async def test_canada_buys_parses_bilingual_csv(settings):
    urls: list[str] = []
    relaxed = settings.model_copy(update={"apply_keyword_prefilter": False})
    csv_text = fixture_text("canada_buys_new.csv")

    def handler(request: httpx.Request) -> httpx.Response:
        urls.append(str(request.url))
        return httpx.Response(
            200, content=csv_text.encode("utf-8"), headers={"content-type": "application/octet-stream"}
        )

    tenders = await fetch(CanadaBuysConnector(relaxed, transport=transport(handler)))
    assert len(urls) == 2, "new + open feeds"
    assert tenders
    tender = tenders[0]
    assert tender.source == "canada_buys"
    assert tender.buyer_country == "CA"
    assert tender.currency == "CAD"
    assert tender.source_url.startswith("https://canadabuys.canada.ca/")
    assert tender.raw_payload["feed"] == "new"
    assert tender.publication_date is not None
    french = [t for t in tenders if t.description and "\n\n" in t.description]
    assert french, "English and French descriptions are both preserved"


async def test_canada_buys_open_feed_can_be_disabled(settings):
    urls: list[str] = []
    tuned = settings.model_copy(update={"enable_canada_buys_open_feed": False})

    def handler(request: httpx.Request) -> httpx.Response:
        urls.append(str(request.url))
        return httpx.Response(
            200,
            content=fixture_text("canada_buys_new.csv").encode("utf-8"),
            headers={"content-type": "text/csv"},
        )

    await fetch(CanadaBuysConnector(tuned, transport=transport(handler)))
    assert len(urls) == 1


# --- AusTender -------------------------------------------------------------


async def test_austender_parses_rss(settings):
    relaxed = settings.model_copy(update={"apply_keyword_prefilter": False})

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, text=fixture_text("austender.xml"), headers={"content-type": "application/rss+xml"}
        )

    tenders = await fetch(AusTenderConnector(relaxed, transport=transport(handler)))
    assert tenders
    tender = tenders[0]
    assert tender.source_url.startswith("https://www.tenders.gov.au/Atm/Show/")
    assert tender.buyer_country == "AU"
    assert tender.publication_date is not None
    assert tender.source_timezone in {"GMT", "UTC"}
    assert tender.reference_number


async def test_austender_refuses_doctype_and_bad_xml(settings):
    def doctype(request: httpx.Request) -> httpx.Response:
        payload = '<?xml version="1.0"?><!DOCTYPE rss [<!ENTITY x "boom">]><rss></rss>'
        return httpx.Response(200, text=payload, headers={"content-type": "application/xml"})

    with pytest.raises(ConnectorError) as exc:
        await fetch(AusTenderConnector(settings, transport=transport(doctype)))
    assert "DOCTYPE" in str(exc.value)

    def broken(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<rss><channel>", headers={"content-type": "application/xml"})

    with pytest.raises(ConnectorError) as exc:
        await fetch(AusTenderConnector(settings, transport=transport(broken)))
    assert "invalid RSS/XML" in str(exc.value)


# --- PNCP ------------------------------------------------------------------


async def test_pncp_uses_documented_params_and_keeps_portuguese(settings):
    seen: list[dict] = []
    relaxed = settings.model_copy(update={"apply_keyword_prefilter": False})

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append({"path": request.url.path, **dict(request.url.params)})
        return httpx.Response(200, json=fixture_json("pncp_page1.json"))

    tenders = await fetch(PncpConnector(relaxed, transport=transport(handler)))
    paths = {call["path"] for call in seen}
    assert "/api/consulta/v1/contratacoes/atualizacao" in paths
    assert "/api/consulta/v1/contratacoes/proposta" in paths
    assert all(call["tamanhoPagina"] == "50" for call in seen)
    assert any(call.get("dataInicial") == "20260818" for call in seen)
    assert all(call["codigoModalidadeContratacao"] == "6" for call in seen)
    assert tenders
    tender = tenders[0]
    assert tender.source_notice_id.count("-") >= 1
    assert tender.language == "pt"
    assert tender.currency == "BRL"
    assert tender.buyer_country == "BR"
    assert any(ch in (tender.description or "") for ch in "çãáéêó"), "Portuguese text is preserved"


async def test_pncp_handles_empty_page_body(settings):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(204, content=b"", headers={"content-type": "application/json"})

    tenders = await fetch(PncpConnector(settings, transport=transport(handler)))
    assert tenders == []


# --- HigherGov -------------------------------------------------------------


def _highergov_handler(urls: list[str]):
    def handler(request: httpx.Request) -> httpx.Response:
        urls.append(str(request.url))
        page = request.url.params.get("page_number")
        name = "highergov_page2.json" if page == "2" else "highergov_page1.json"
        return httpx.Response(200, json=fixture_json(name))

    return handler


async def test_highergov_needs_an_api_key(settings):
    connector = HigherGovConnector(settings)
    assert connector.requires_api_key is True
    assert "HIGHERGOV_API_KEY" in connector.unavailable_reason()


async def test_highergov_refuses_to_run_without_a_saved_search(settings):
    """The whole design turns on this, so it is a refusal and not a warning.

    The API has no free-text search on any endpoint and silently ignores
    unknown parameters, so with no search_id the only thing left to ask for is
    an unfiltered date scan. That is not a degraded mode: one day of postings
    is ~5,500 records against a 10,000-record *monthly* quota, and 0 of 300
    sampled records reached the 50-point relevance band. Running anyway would
    spend the whole allowance on noise.
    """
    keyed = settings.model_copy(update={"highergov_api_key": "hg-key-not-real"})
    connector = HigherGovConnector(keyed)
    reason = connector.unavailable_reason()
    assert reason is not None, "a key alone must not be enough to run"
    assert "HIGHERGOV_SEARCH_ID" in reason
    assert "no keyword parameter" in reason


async def test_highergov_pagination_window_prefilter_and_normalization(highergov_settings):
    """Two pages, and only the records that survive window *and* prefilter.

    The fixtures carry four real records: the genuine hit, a chemical-purchase
    false positive whose text merely requires an SDS on delivery, one posted
    outside the window, and one on page two.
    """
    urls: list[str] = []
    tenders = await fetch(
        HigherGovConnector(highergov_settings, transport=transport(_highergov_handler(urls)))
    )

    assert len(urls) == 2, "the `next` link must be followed"
    assert all("search_id=OvSsysuZMmV1UnmB1s0hJ" in u for u in urls)
    titles = [t.title for t in tenders]
    assert titles == ["Chemical Management Managed Service", "EHS Management System Implementation"]
    assert "SDS Management Platform Renewal" not in titles, "posted outside the window"
    assert "ADHESIVE" not in titles, "chemical purchase, dropped by the title prefilter"

    tender = tenders[0]
    assert tender.source == "highergov"
    assert tender.source_notice_id
    assert tender.buyer_country == "US"
    assert tender.currency == "USD"
    assert tender.language == "en"
    assert tender.deadline == datetime(2026, 9, 15)
    assert tender.status == "open"
    assert tender.reference_number
    assert {"scheme": "NAICS", "code": "541690"} in tender.classification_codes
    # `path` is already absolute; prefixing the host would corrupt it.
    assert tender.source_url.startswith("https://www.highergov.com/")
    assert "highergov.comhttps://" not in tender.source_url
    # HTML entities arrive inside otherwise-plain text fields.
    assert "&rsquo;" not in (tender.description or "")
    assert "&amp;" not in (tender.description or "")
    assert "SDS library" in tender.description


async def test_highergov_never_stores_the_api_key(highergov_settings):
    """Every record arrives with the caller's own key inside document_path.

    Stored verbatim it would be written to the database and rendered in the
    dashboard, so document_urls, raw_payload and source_url are all scrubbed.
    This is the one connector where the *response* carries the credential, not
    just the request.
    """
    urls: list[str] = []
    tenders = await fetch(
        HigherGovConnector(highergov_settings, transport=transport(_highergov_handler(urls)))
    )

    assert tenders, "need a record to inspect"
    key = highergov_settings.highergov_api_key
    for tender in tenders:
        blob = tender.model_dump_json()
        assert key not in blob, "the API key reached a stored field"
        assert "api_key=***" in blob, "document_path should survive, redacted"
        for url in tender.document_urls:
            assert key not in url


async def test_highergov_window_matches_either_date(highergov_settings):
    """posted_date OR captured_date - 15 of 55 live records had them differ.

    A notice posted weeks ago can be captured by HigherGov today; filtering on
    posted_date alone would silently drop it.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        payload = fixture_json("highergov_page1.json")
        # One record only, so the assertion is about the date logic alone.
        record = payload["results"][0]
        # Posted long before the window, captured inside it.
        record["posted_date"] = "2026-01-05"
        record["captured_date"] = "2026-08-19"
        payload["results"] = [record]
        payload["links"]["next"] = None
        return httpx.Response(200, json=payload)

    tenders = await fetch(HigherGovConnector(highergov_settings, transport=transport(handler)))
    assert [t.title for t in tenders] == ["Chemical Management Managed Service"]
    assert tenders[0].publication_date == datetime(2026, 1, 5)
    assert tenders[0].source_updated_at == datetime(2026, 8, 19), "captured_date is what 'new to us' means"


# --- registry --------------------------------------------------------------


def test_registry_exposes_every_required_source(settings):
    assert set(SOURCE_NAMES) == {
        "ted",
        "sam",
        "find_a_tender",
        "contracts_finder",
        "world_bank",
        "canada_buys",
        "austender",
        "pncp",
        "highergov",
    }
    assert len(build_all(settings)) == len(SOURCE_NAMES)
    assert build_connector("ted", settings).display_name == "EU TED"
    with pytest.raises(KeyError):
        build_connector("nope", settings)
    catalog = {entry["name"]: entry for entry in source_catalog(settings)}
    # False because the bulk extract is the default transport and needs no key.
    assert catalog["sam"]["requires_api_key"] is False
    assert catalog["ted"]["requires_api_key"] is False
    assert all(entry["notes"] for entry in catalog.values())


def test_per_source_env_switches(settings):
    disabled = settings.model_copy(update={"enable_ted": False})
    assert build_connector("ted", disabled).enabled is False
    assert build_connector("pncp", disabled).enabled is True


def test_content_hash_changes_with_content():
    base = NormalizedTender(source="ted", source_notice_id="1", title="A", description="x")
    same = NormalizedTender(source="ted", source_notice_id="1", title="A", description="x")
    other = NormalizedTender(source="ted", source_notice_id="1", title="A", description="y")
    assert base.content_hash == same.content_hash
    assert base.content_hash != other.content_hash


def test_datetime_parsing_is_utc_with_source_timezone():
    value, tz = parse_datetime("2026-08-19T18:01:43+01:00")
    assert value == datetime(2026, 8, 19, 17, 1, 43)
    assert tz == "+01:00"
    assert parse_datetime("2026-08-01+02:00")[0] == datetime(2026, 7, 31, 22, 0)
    assert parse_datetime("06-Aug-2026", ("%d-%b-%Y",))[0] == datetime(2026, 8, 6)
    assert parse_datetime("") == (None, None)
    assert parse_datetime("not a date") == (None, None)


def test_window_helper_orders_dates(settings):
    connector = TedConnector(settings)
    assert connector.clamp_window(DATE_TO, DATE_FROM) == (DATE_FROM, DATE_TO)
    assert connector.window_days(DATE_FROM, DATE_TO + timedelta(days=1)) == 4
