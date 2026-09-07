"""Germany's federal open-data feed - the only source carrying sub-threshold notices.

The fixture is a trimmed copy of the real 2026-07-29 export and contains the notice
this connector was built for: IFW Dresden's "Softwarelösung zur
Chemikalienbewirtschaftung", which never reached TED and which the monitor missed.
"""

from __future__ import annotations

import datetime as dt
import pathlib

import httpx
import pytest

from app.connectors.base import ConnectorError
from app.connectors.oeffentlichevergabe import OeffentlicheVergabeConnector

DAY = dt.datetime(2026, 7, 29)
FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "oeffentlichevergabe_day.zip"


def _connector(settings, handler, **overrides):
    settings = settings.model_copy(update={"max_pages_per_source": 1, **overrides})
    return OeffentlicheVergabeConnector(settings, transport=httpx.MockTransport(handler))


def _serve_fixture(request: httpx.Request) -> httpx.Response:
    assert request.url.params["format"] == "ocds.zip"
    if request.url.params["pubDay"] == "2026-07-29":
        return httpx.Response(200, content=FIXTURE.read_bytes())
    return httpx.Response(404)


@pytest.mark.anyio
async def test_parses_a_german_below_threshold_notice(settings):
    connector = _connector(settings, _serve_fixture, apply_keyword_prefilter=False)
    tenders = await connector.fetch(DAY, DAY)

    found = [t for t in tenders if "Chemikalienbewirtschaftung" in t.title]
    assert len(found) == 1
    tender = found[0]
    assert tender.source == "oeffentlichevergabe"
    assert tender.source_notice_id == "25641424"
    assert tender.reference_number == "ocds-mnwr74-25641424"
    assert tender.buyer_name == "IFW Dresden e.V."
    # The shared OCDS normaliser defaults to "GB" (it was written for the UK feeds);
    # the connector must resolve the real country from the delivery address.
    assert tender.buyer_country == "DEU"
    assert tender.language == "deu"
    # The notice lives on evergabe.de, which we may not scrape - but the federal
    # feed hands us the link for free.
    assert "evergabe.de" in tender.source_url


@pytest.mark.anyio
async def test_deadline_is_recovered_from_the_notice_prose(settings):
    """UVgO notices carry no tenderPeriod - the date is inside the description."""
    connector = _connector(settings, _serve_fixture, apply_keyword_prefilter=False)
    tenders = await connector.fetch(DAY, DAY)
    tender = next(t for t in tenders if "Chemikalienbewirtschaftung" in t.title)
    # "i) Angebotsfrist: 10.08.2026, 10:00 Uhr"
    assert tender.deadline == dt.datetime(2026, 8, 10, 10, 0)


@pytest.mark.anyio
async def test_prefilter_keeps_the_chemical_notice_and_drops_the_building_works(settings):
    connector = _connector(settings, _serve_fixture, apply_keyword_prefilter=True)
    titles = [t.title for t in await connector.fetch(DAY, DAY)]
    assert any("Chemikalienbewirtschaftung" in t for t in titles)
    assert not any("Gebäudeautomation" in t for t in titles)


@pytest.mark.anyio
async def test_a_malformed_member_does_not_drop_the_rest_of_the_day(settings):
    """The fixture carries a deliberately broken JSON member."""
    connector = _connector(settings, _serve_fixture, apply_keyword_prefilter=False)
    assert len(await connector.fetch(DAY, DAY)) == 2


@pytest.mark.anyio
async def test_a_day_with_no_export_is_not_a_failure(settings):
    connector = _connector(settings, lambda request: httpx.Response(404))
    assert await connector.fetch(DAY, DAY) == []


@pytest.mark.anyio
async def test_a_non_zip_body_is_a_connector_error(settings):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="Not Acceptable: acceptable representations are...")

    connector = _connector(settings, handler)
    with pytest.raises(ConnectorError, match="not a ZIP"):
        await connector.fetch(DAY, DAY)


@pytest.mark.anyio
async def test_window_is_walked_a_day_at_a_time_newest_first(settings):
    """One request per publication day, capped by max_pages_per_source."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.params["pubDay"])
        return httpx.Response(404)

    connector = _connector(settings, handler, max_pages_per_source=3)
    await connector.fetch(dt.datetime(2026, 7, 20), dt.datetime(2026, 7, 29))
    assert seen == ["2026-07-29", "2026-07-28", "2026-07-27"]


@pytest.mark.anyio
async def test_a_truncated_archive_is_a_retryable_error_not_a_crash(settings):
    """A cut-off download still starts with the ZIP magic, so only opening it fails."""
    truncated = FIXTURE.read_bytes()[:400]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=truncated)

    connector = _connector(settings, handler)
    with pytest.raises(ConnectorError, match="truncated"):
        await connector.fetch(DAY, DAY)
