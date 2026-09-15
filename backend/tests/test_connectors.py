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
from app.connectors.keywords import SEARCH_PHRASES
from app.connectors.pncp import PncpConnector
from app.connectors.registry import SOURCE_NAMES, build_all, build_connector, source_catalog
from app.connectors.sam import SamGovConnector
from app.connectors.spend_network import SpendNetworkConnector
from app.connectors.ted import TedConnector
from app.connectors.world_bank import WorldBankConnector
from tests.conftest import fixture_json, fixture_text

DATE_FROM = datetime(2026, 8, 18)
DATE_TO = datetime(2026, 8, 21)

# Deadlines in the fixtures sit in 2030 deliberately. `status_from_deadline`
# compares against the real clock, so a fixture whose deadline was merely
# "soon" when it was captured turns three passing tests red on the day it
# arrives - which is exactly what happened on 2026-09-15, to SAM, the SAM
# extract and HigherGov at once. A date far enough out is not laziness; it is
# the difference between asserting the mapping and asserting today's date.


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
            "responseDeadLine": "2030-09-15T17:00:00-04:00",
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
    assert tender.deadline == datetime(2030, 9, 15, 21, 0)
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
    assert tender.deadline == datetime(2030, 9, 15, 21, 0)
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
    assert tender.deadline == datetime(2030, 9, 15)
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


# --- Spend Network ---------------------------------------------------------
#
# The window is DATE_FROM..DATE_TO (2026-08-18..21), walked newest day first.
# The busiest day here has two pages at the fixture's page size of 2; the rest
# are one page or empty. That shape is what lets these tests say something about
# day-chunking, the throttle and the backfill separately.

SN_BUSY_DAY = "2026-08-21"
SN_OLD_DAY = "2026-08-18"


def _spend_network_pages() -> dict[tuple[str, int], str]:
    return {
        (SN_BUSY_DAY, 0): "spend_network_day_page1.json",
        (SN_BUSY_DAY, 2): "spend_network_day_page2.json",
        (SN_OLD_DAY, 0): "spend_network_day_older.json",
    }


def _spend_network_handler(calls: list[httpx.Request], refuse: list[tuple[str, int]] | None = None):
    """Sign-in, then a fixture per (day, offset). Empty for any day not listed.

    ``refuse`` names (day, offset) pairs that answer 403 *once each* before
    serving, which is how the throttle is modelled: it is not a failure mode
    here, it is the ordinary cost of paging a whole day.
    """
    pages = _spend_network_pages()
    outstanding = list(refuse or [])

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path.endswith("/login/access-token"):
            return httpx.Response(
                200,
                json={
                    "access_token": "sn-token-not-real",
                    "token_type": "bearer",
                    "expiration_date": "2026-09-23T04:33:25.900350+00:00",
                },
            )
        day = request.url.params.get("release_date__gte")
        offset = int(request.url.params.get("offset", 0))
        if (day, offset) in outstanding:
            outstanding.remove((day, offset))
            return httpx.Response(403, text="<html><body>403 Forbidden</body></html>")
        if request.url.params.get("search_term__is"):
            return httpx.Response(200, json=fixture_json("spend_network_backfill.json"))
        name = pages.get((day, offset))
        if name is None:
            return httpx.Response(200, json={"offset": offset, "limit": 2, "result_count": 0, "results": []})
        return httpx.Response(200, json=fixture_json(name))

    return handler


def _sn_searches(calls: list[httpx.Request]) -> list[httpx.Request]:
    return [c for c in calls if not c.url.path.endswith("/login/access-token")]


def _sn_feed_calls(calls: list[httpx.Request]) -> list[httpx.Request]:
    """The whole-feed requests, excluding the keyword backfill pass."""
    return [c for c in _sn_searches(calls) if not c.url.params.get("search_term__is")]


async def test_spend_network_needs_both_halves_of_the_account(settings):
    """An email or a password alone is not a credential, and says which is missing."""
    connector = SpendNetworkConnector(settings)
    assert connector.requires_api_key is True
    assert "SPEND_NETWORK_EMAIL" in connector.unavailable_reason()

    half = settings.model_copy(update={"spend_network_email": "someone@example.invalid"})
    reason = SpendNetworkConnector(half).unavailable_reason()
    assert reason is not None, "an email alone must not be enough to run"
    assert "SPEND_NETWORK_PASSWORD" in reason

    both = half.model_copy(update={"spend_network_password": "sn-password-not-real"})
    assert SpendNetworkConnector(both).unavailable_reason() is None


async def test_spend_network_takes_the_whole_feed_and_does_not_ask_the_api_to_search(
    spend_network_settings,
):
    """The filter is ours now, which means no `search_term__is` on the feed pass.

    This is the decision the whole connector turns on. Sending a search term
    again would silently shrink the feed back to whatever the vendor's engine
    matched - 108 notices in a month against the ~132,000 actually published -
    and nothing downstream could tell the difference between "the feed was
    quiet" and "we asked for a hundredth of it".
    """
    calls: list[httpx.Request] = []
    await fetch(
        SpendNetworkConnector(spend_network_settings, transport=transport(_spend_network_handler(calls)))
    )

    feed = _sn_feed_calls(calls)
    assert feed, "no feed requests were made at all"
    for call in feed:
        assert "search_term__is" not in call.url.params, "the API was asked to filter for us"


async def test_spend_network_walks_the_window_one_day_at_a_time_newest_first(spend_network_settings):
    """Paging stops dead at 10,000 records, so the day is the chunk.

    A multi-day query would silently lose everything past the ceiling on any
    busy window. Newest first so a sweep cut short by its budget has covered the
    end anybody is waiting on.
    """
    calls: list[httpx.Request] = []
    await fetch(
        SpendNetworkConnector(spend_network_settings, transport=transport(_spend_network_handler(calls)))
    )

    feed = _sn_feed_calls(calls)
    # Every request asks for exactly one day.
    for call in feed:
        assert call.url.params["release_date__gte"] == call.url.params["release_date__lte"]
    days = list(dict.fromkeys(c.url.params["release_date__gte"] for c in feed))
    assert days == ["2026-08-21", "2026-08-20", "2026-08-19", "2026-08-18"]
    # The busy day is paged to exhaustion; the empty ones cost one request each.
    busy = [int(c.url.params["offset"]) for c in feed if c.url.params["release_date__gte"] == SN_BUSY_DAY]
    assert busy == [0, 2, 4], "a day must be paged until it runs out, not sampled"


async def test_spend_network_filters_on_our_own_terms_before_storing(spend_network_settings):
    """The whole point of paging the feed: we decide what is worth keeping.

    The fixture's busy day carries a road-resurfacing notice, which is what
    almost all of a global procurement feed is. It matches no term and must not
    reach the database. The one beside it matches "health and safety" in its
    body and must.
    """
    calls: list[httpx.Request] = []
    tenders = await fetch(
        SpendNetworkConnector(spend_network_settings, transport=transport(_spend_network_handler(calls)))
    )

    titles = [t.title or "" for t in tenders]
    assert not any("Fahrbahndecke" in t for t in titles), "a works contract was stored"
    assert any("P1218" in t for t in titles), "a topical notice was dropped"
    assert all(t.source == "spend_network" for t in tenders)


async def test_spend_network_matches_the_body_not_only_the_title(spend_network_settings):
    """Title-only prefiltering would throw away five sixths of what matters here.

    Measured over 108 stored notices: matching title alone kept 12 and lost ten
    of the twelve that scored 50 or better, because these buyers put a
    procurement reference in the title and the subject in the description. The
    kept records below match on body text, not on their titles - which is the
    whole reason this connector does not copy sam.py's title-only prefilter.
    """
    calls: list[httpx.Request] = []
    tenders = await fetch(
        SpendNetworkConnector(spend_network_settings, transport=transport(_spend_network_handler(calls)))
    )
    kept = next(t for t in tenders if "P1218" in (t.title or ""))
    from app.connectors.keywords import looks_relevant

    assert not looks_relevant(kept.title), "pick a fixture whose title does not match"
    assert looks_relevant(kept.description), "...but whose body does"


async def test_spend_network_prefilter_cannot_be_switched_off_by_the_general_flag(
    spend_network_settings,
):
    """APPLY_KEYWORD_PREFILTER=false means something reasonable for a national feed.

    For a global aggregator it means ~1.46M notices a year at ~25KB each, and
    the flag's name says nothing about this source. The escape hatch is its own
    setting, so nobody arrives at 37GB by flipping something else.
    """
    loose = spend_network_settings.model_copy(update={"apply_keyword_prefilter": False})
    calls: list[httpx.Request] = []
    tenders = await fetch(SpendNetworkConnector(loose, transport=transport(_spend_network_handler(calls))))
    assert not any("Fahrbahndecke" in t.title for t in tenders)

    deliberate = spend_network_settings.model_copy(update={"spend_network_store_unfiltered": True})
    calls = []
    everything = await fetch(
        SpendNetworkConnector(deliberate, transport=transport(_spend_network_handler(calls)))
    )
    assert any("Fahrbahndecke" in t.title for t in everything), "the deliberate switch did nothing"


async def test_spend_network_waits_out_the_throttle_and_retries_the_same_page(
    spend_network_settings,
):
    """A 403 mid-feed is the ordinary cost of a day, not a failed sweep.

    A mean day is 40 requests against a budget of about 30 per five minutes, so
    every day trips it. What must never happen is the interrupted page being
    skipped: that is a hole in the window nothing downstream could detect.
    """
    calls: list[httpx.Request] = []
    handler = _spend_network_handler(calls, refuse=[(SN_BUSY_DAY, 2)])
    tenders = await fetch(SpendNetworkConnector(spend_network_settings, transport=transport(handler)))

    feed = _sn_feed_calls(calls)
    busy = [int(c.url.params["offset"]) for c in feed if c.url.params["release_date__gte"] == SN_BUSY_DAY]
    assert busy == [0, 2, 2, 4], "the refused offset must be retried, not stepped over"
    # And the page behind the 403 actually arrived.
    assert any("P1218" in (t.title or "") for t in tenders), "the retried page was lost"


async def test_spend_network_stops_when_it_runs_out_of_patience(spend_network_settings):
    """Bounded waiting, so a sweep cannot sit for hours if the budget shrinks."""
    calls: list[httpx.Request] = []
    impatient = spend_network_settings.model_copy(update={"spend_network_max_throttle_waits": 0})
    handler = _spend_network_handler(calls, refuse=[(SN_BUSY_DAY, 0)])
    tenders = await fetch(SpendNetworkConnector(impatient, transport=transport(handler)))

    feed = _sn_feed_calls(calls)
    assert [int(c.url.params["offset"]) for c in feed] == [0], "with no waits left it must not retry"
    # And it must not walk on to the next day either: the block is account-wide,
    # so every remaining day would spend a request to be refused again.
    assert len({c.url.params["release_date__gte"] for c in feed}) == 1
    assert not [c for c in _sn_searches(calls) if c.url.params.get("search_term__is")], "backfill ran anyway"
    assert tenders == [], "nothing was reachable, so nothing is claimed"


async def test_spend_network_stops_at_its_request_budget(spend_network_settings):
    """The guard against a 90-day sweep asked for by accident: 1,200 requests, four hours."""
    calls: list[httpx.Request] = []
    tiny = spend_network_settings.model_copy(update={"spend_network_max_requests_per_sweep": 2})
    await fetch(SpendNetworkConnector(tiny, transport=transport(_spend_network_handler(calls))))
    assert len(_sn_searches(calls)) == 2


async def test_spend_network_pays_two_requests_for_the_late_arrivals(spend_network_settings):
    """The only date filter is the upstream portal's publication date.

    A notice released last month and aggregated today falls outside any sane
    window, and paging the whole feed back thirty days to catch it would cost
    about four hours. One keyword query costs two requests and catches most of
    it - and it is the one request that *does* carry quoted phrases.
    """
    calls: list[httpx.Request] = []
    await fetch(
        SpendNetworkConnector(spend_network_settings, transport=transport(_spend_network_handler(calls)))
    )

    backfill = [c for c in _sn_searches(calls) if c.url.params.get("search_term__is")]
    assert len(backfill) == 1
    term = backfill[0].url.params["search_term__is"]
    assert term.startswith('"') and " OR " in term, "the backfill pass lost its quoting"
    # It covers the days *before* the window, which the day walk already did.
    assert backfill[0].url.params["release_date__lte"] == "2026-08-17"
    assert backfill[0].url.params["release_date__gte"] == "2026-07-19"


async def test_spend_network_skips_the_backfill_when_it_is_switched_off(spend_network_settings):
    off = spend_network_settings.model_copy(update={"spend_network_backfill_days": 0})
    calls: list[httpx.Request] = []
    await fetch(SpendNetworkConnector(off, transport=transport(_spend_network_handler(calls))))
    assert not [c for c in _sn_searches(calls) if c.url.params.get("search_term__is")]


def test_spend_network_quotes_every_search_phrase():
    """The backfill pass is the only search left, and it is quoted or it is the feed.

    Measured 2026-09-15: unquoted, `safety data sheet` is ORed word by word and
    matched 4,770 notices in a fortnight; quoted, it matched 23. The API reports
    no error either way, so nothing but this test notices if the quotes go.
    """
    query = SpendNetworkConnector.build_query(("safety data sheet", "chemical management"))
    assert query == '"safety data sheet" OR "chemical management"'

    full = SpendNetworkConnector.build_query()
    assert full.startswith('"'), "a bare leading word would be ORed into the feed"
    assert " OR " in full
    for phrase in SEARCH_PHRASES:
        assert f'"{phrase}"' in full, f"{phrase} reached the API unquoted"


async def test_spend_network_signs_in_once_per_sweep(spend_network_settings):
    calls: list[httpx.Request] = []
    await fetch(
        SpendNetworkConnector(spend_network_settings, transport=transport(_spend_network_handler(calls)))
    )
    logins = [c for c in calls if c.url.path.endswith("/login/access-token")]
    assert len(logins) == 1
    assert all(c.headers["authorization"] == "Bearer sn-token-not-real" for c in _sn_searches(calls))


async def test_spend_network_reuses_its_token_across_sweeps(spend_network_settings):
    """The cache earns its keep between sweeps, not between pages.

    One ``fetch`` signs in once whether or not anything is cached - the token is
    taken before the day walk - so asserting "one login per sweep" says nothing
    about the cache. Two sweeps is the question it answers, and the token lives
    eight days.
    """
    calls: list[httpx.Request] = []
    handler = transport(_spend_network_handler(calls))
    await fetch(SpendNetworkConnector(spend_network_settings, transport=handler))
    await fetch(SpendNetworkConnector(spend_network_settings, transport=handler))

    logins = [c for c in calls if c.url.path.endswith("/login/access-token")]
    assert len(logins) == 1, "the second sweep re-minted a token that had eight days left"


async def test_spend_network_signs_in_again_when_the_password_changes(spend_network_settings):
    """A rotated credential must not be served a token minted from the old one."""
    calls: list[httpx.Request] = []
    handler = transport(_spend_network_handler(calls))
    await fetch(SpendNetworkConnector(spend_network_settings, transport=handler))
    rotated = spend_network_settings.model_copy(update={"spend_network_password": "sn-password-rotated"})
    await fetch(SpendNetworkConnector(rotated, transport=handler))

    logins = [c for c in calls if c.url.path.endswith("/login/access-token")]
    assert len(logins) == 2, "the cache key must cover the credential, not just the address"


async def test_spend_network_drops_a_record_with_no_ocid(spend_network_settings):
    """The OCDS id is the only field unique across forty upstream portals.

    tender_id collides between them, so a record without an ocid cannot be
    stored, deduplicated or looked up again - it is skipped, not invented.
    """
    calls: list[httpx.Request] = []
    tenders = await fetch(
        SpendNetworkConnector(spend_network_settings, transport=transport(_spend_network_handler(calls)))
    )
    assert "Record with no OCDS id" not in [t.title for t in tenders]
    assert all(t.source_notice_id.startswith("ocds-") for t in tenders)


async def test_spend_network_normalizes_a_real_record(spend_network_settings):
    calls: list[httpx.Request] = []
    tenders = await fetch(
        SpendNetworkConnector(spend_network_settings, transport=transport(_spend_network_handler(calls)))
    )
    german = next(t for t in tenders if t.source_notice_id == "ocds-0c46vo-0125-2605221")

    assert german.title.startswith("2026-0563 Erneuerung")
    assert german.buyer_country == "DE"
    assert german.language == "de"
    assert german.delivery_location == "Germany"
    assert german.publication_date == datetime(2026, 9, 14)
    assert german.deadline == datetime(2026, 10, 6)
    assert german.status == "open"
    assert german.procurement_stage == "tender"
    assert german.source_url.startswith("https://www.dtvp.de/")
    # Absent values arrive as 0.0, not null. Zero means "not stated".
    assert german.estimated_value is None
    # The upstream portal's own timezone label, kept beside the UTC value.
    assert german.source_timezone == "UTC+02:00"


async def test_spend_network_keeps_a_predicted_cpv_apart_from_a_published_one(spend_network_settings):
    """`cpv_aug_data` is a model's guess and arrives on every record.

    `cpv_codes` is what the buyer actually published and arrived on 20 of 97.
    Filing both under "CPV" would let a prediction pass downstream as a
    classification the buyer made.
    """
    calls: list[httpx.Request] = []
    tenders = await fetch(
        SpendNetworkConnector(spend_network_settings, transport=transport(_spend_network_handler(calls)))
    )
    with_cpv = next(t for t in tenders if any(c["scheme"] == "CPV" for c in t.classification_codes))
    schemes = {c["scheme"] for c in with_cpv.classification_codes}
    assert "CPV" in schemes and "CPV-PREDICTED" in schemes
    predicted = [c for c in with_cpv.classification_codes if c["scheme"] == "CPV-PREDICTED"]
    assert all("confidence" in c for c in predicted)
    published = [c for c in with_cpv.classification_codes if c["scheme"] == "CPV"]
    assert all("confidence" not in c for c in published), "a published code has no confidence to state"


def test_spend_network_falls_back_to_a_converted_value(spend_network_settings):
    """48 of 97 records carried a currency and only 35 carried an amount.

    Neither field implies the other, so when the native amount is missing the
    connector takes Spend Network's own conversion and stores the currency it
    actually took - never the native currency beside a converted number.

    Asserted against ``_normalize`` rather than through ``fetch`` on purpose:
    this record does not survive the term filter, and no record in a 2,027-notice
    pool both survived it and lacked a title. Normalisation has to be right for
    records the pipeline drops, because the term list is the one thing here that
    is expected to change.
    """
    connector = SpendNetworkConnector(spend_network_settings)
    raw = fixture_json("spend_network_day_older.json")["results"][0]
    award = connector._normalize(raw)

    # The record states AUD but carries no tender_amount, so GBP is what was used.
    assert award.currency == "GBP"
    assert award.estimated_value and award.estimated_value > 0
    # 4 of 97 records carried no title; the ocid is a poor label but a true one.
    assert award.title == award.source_notice_id
    assert award.status == "closed"
    assert award.procurement_stage == "award"


async def test_spend_network_drops_that_same_award_from_the_pipeline(spend_network_settings):
    """And it is dropped, which is the term list doing its job on a real record.

    A fuel-supply award mentioning safety in its body is exactly the kind of
    notice the old server-side search returned and this system has no use for.
    """
    calls: list[httpx.Request] = []
    tenders = await fetch(
        SpendNetworkConnector(spend_network_settings, transport=transport(_spend_network_handler(calls)))
    )
    assert not any(t.procurement_stage == "award" for t in tenders)


async def test_spend_network_never_stores_the_password_or_the_token(spend_network_settings):
    """The credential is an account, so the failure mode is different.

    A key can only leak through a URL the connector built; a password could leak
    through anything that echoes the sign-in. Nothing that reaches the database
    may contain either half of it.
    """
    calls: list[httpx.Request] = []
    tenders = await fetch(
        SpendNetworkConnector(spend_network_settings, transport=transport(_spend_network_handler(calls)))
    )
    assert tenders, "need a record to inspect"
    for tender in tenders:
        blob = tender.model_dump_json()
        assert spend_network_settings.spend_network_password not in blob
        assert spend_network_settings.spend_network_email not in blob
        assert "sn-token-not-real" not in blob
    # And the request that carries it is a POST body, not a query string.
    login = next(c for c in calls if c.url.path.endswith("/login/access-token"))
    assert spend_network_settings.spend_network_password not in str(login.url)


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
        "oeffentlichevergabe",
        "spend_network",
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
