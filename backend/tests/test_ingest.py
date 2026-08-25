"""Upserts, content-hash change detection, scoring and fetch-run isolation."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.connectors.base import ConnectorError, NormalizedTender, TenderConnector
from app.models import FetchRun, Tender
from app.services import ingest
from app.services.relevance import get_engine

NOW = datetime(2026, 8, 21, 12, 0)


def make_tender(**overrides) -> NormalizedTender:
    payload = {
        "source": "ted",
        "source_notice_id": "notice-1",
        "source_url": "https://ted.europa.eu/en/notice/-/detail/1-2026",
        "reference_number": "2026/S 1-000001",
        "title": "Cloud hosted SDS management and chemical inventory platform",
        "description": "Software as a service platform for safety data sheet management, chemical "
        "inventory and GHS labelling. Subscription licensing and API integration required.",
        "buyer_name": "Environment Agency",
        "buyer_country": "DEU",
        "publication_date": NOW - timedelta(days=1),
        "deadline": NOW + timedelta(days=20),
        "status": "open",
        "procurement_stage": "tender",
        "notice_type": "cn-standard",
        "estimated_value": 500000.0,
        "currency": "EUR",
        "classification_codes": [{"scheme": "CPV", "code": "48000000"}],
        "document_urls": ["https://ted.europa.eu/en/notice/1-2026/pdf"],
        "language": "eng",
        "raw_payload": {"publication-number": "1-2026"},
    }
    payload.update(overrides)
    return NormalizedTender(**payload)


class FakeConnector(TenderConnector):
    source_name = "fake"
    display_name = "Fake"

    def __init__(self, settings, tenders=None, error=None, unavailable=None):
        super().__init__(settings)
        self._tenders = tenders or []
        self._error = error
        self._unavailable = unavailable

    def unavailable_reason(self):
        return self._unavailable

    async def fetch(self, date_from, date_to):
        if self._error:
            raise self._error
        return list(self._tenders)


# --- upserts ---------------------------------------------------------------


def test_insert_then_reobserve_is_not_a_duplicate(db_session):
    tender = make_tender()
    assert ingest.upsert_tender(db_session, tender, now=NOW) == "created"
    assert ingest.upsert_tender(db_session, tender, now=NOW + timedelta(hours=6)) == "unchanged"
    assert db_session.execute(select(func.count(Tender.id))).scalar_one() == 1

    row = db_session.execute(select(Tender)).scalar_one()
    assert row.first_seen_at == NOW
    assert row.last_seen_at == NOW + timedelta(hours=6)


def test_content_hash_detects_a_real_update(db_session):
    ingest.upsert_tender(db_session, make_tender(), now=NOW)
    original = db_session.execute(select(Tender)).scalar_one()
    original_hash = original.content_hash

    amended = make_tender(title="Amended: cloud hosted SDS management platform", estimated_value=750000.0)
    assert ingest.upsert_tender(db_session, amended, now=NOW + timedelta(days=1)) == "updated"

    row = db_session.execute(select(Tender)).scalar_one()
    assert row.content_hash != original_hash
    assert row.title.startswith("Amended")
    assert row.estimated_value == 750000.0
    assert row.first_seen_at == NOW, "first_seen_at must never move"
    assert row.last_seen_at == NOW + timedelta(days=1)
    assert db_session.execute(select(func.count(Tender.id))).scalar_one() == 1


def test_same_notice_id_from_two_sources_is_two_rows(db_session):
    ingest.upsert_tender(db_session, make_tender(source="ted"), now=NOW)
    ingest.upsert_tender(db_session, make_tender(source="sam"), now=NOW)
    assert db_session.execute(select(func.count(Tender.id))).scalar_one() == 2


def test_every_stored_notice_is_scored(db_session):
    ingest.upsert_tender(db_session, make_tender(), now=NOW)
    row = db_session.execute(select(Tender)).scalar_one()
    assert row.relevance_score > 0
    assert row.relevance_category
    assert row.fit_status
    assert row.deployment_fit
    assert row.relevance_reasons
    assert isinstance(row.disqualifiers, list)
    assert isinstance(row.review_flags, list)


def test_batch_survives_one_unstorable_record(db_session):
    broken = make_tender(source_notice_id="broken", raw_payload={"unserializable": {1, 2, 3}})
    stats = ingest.store_tenders(db_session, [make_tender(), broken, make_tender(source_notice_id="ok-2")])
    assert stats.created == 2
    assert stats.failed == 1
    assert stats.received == 3
    stored = {t.source_notice_id for t in db_session.execute(select(Tender)).scalars()}
    assert stored == {"notice-1", "ok-2"}


def test_rescore_all_reapplies_the_engine(db_session):
    ingest.upsert_tender(db_session, make_tender(), now=NOW)
    row = db_session.execute(select(Tender)).scalar_one()
    row.relevance_score = 0
    row.relevance_reasons = []
    db_session.commit()

    assert ingest.rescore_all(db_session, get_engine(None)) == 1
    row = db_session.execute(select(Tender)).scalar_one()
    assert row.relevance_score > 0
    assert row.relevance_reasons


# --- window ----------------------------------------------------------------


def test_window_enforces_the_overlapping_lookback(settings):
    start, end = ingest.window(0, settings)
    assert (end - start) >= timedelta(hours=settings.fetch_min_lookback_hours)
    start, end = ingest.window(30, settings)
    assert (end - start) == timedelta(days=30)


# --- fetch runs ------------------------------------------------------------


@pytest.fixture
def patched_session(monkeypatch, db_session):
    monkeypatch.setattr(ingest, "SessionLocal", db_session.info["factory"])
    return db_session


async def test_one_failing_source_does_not_fail_the_run(patched_session, settings, monkeypatch):
    def build(source, settings_arg=None, transport=None, **kw):
        if source == "ted":
            return FakeConnector(settings, tenders=[make_tender()])
        if source == "sam":
            return FakeConnector(settings, error=ConnectorError("sam", "HTTP 500 from source", status=500))
        return FakeConnector(settings, unavailable="no key configured")

    monkeypatch.setattr(ingest, "build_connector", build)
    run_ids = await ingest.run_fetch(["ted", "sam", "world_bank"], days_back=1, settings=settings)
    assert len(run_ids) == 3

    runs = {r.source: r for r in patched_session.execute(select(FetchRun)).scalars()}
    assert runs["ted"].status == "success"
    assert runs["ted"].records_created == 1
    assert runs["sam"].status == "failed"
    assert "HTTP 500" in runs["sam"].error_message
    assert runs["world_bank"].status == "skipped"
    assert runs["world_bank"].error_message == "no key configured"
    # The healthy source still stored its data.
    assert patched_session.execute(select(func.count(Tender.id))).scalar_one() == 1


async def test_connector_crash_is_recorded_not_raised(patched_session, settings, monkeypatch):
    monkeypatch.setattr(
        ingest,
        "build_connector",
        lambda source, s=None, transport=None, **kw: FakeConnector(settings, error=RuntimeError("boom")),
    )
    await ingest.run_fetch(["ted"], days_back=1, settings=settings)
    run = patched_session.execute(select(FetchRun)).scalar_one()
    assert run.status == "failed"
    assert "RuntimeError: boom" in run.error_message
    assert run.finished_at is not None


async def test_partial_status_when_some_records_fail(patched_session, settings, monkeypatch):
    tenders = [make_tender(), make_tender(source_notice_id="bad", raw_payload={"x": {1}})]
    monkeypatch.setattr(
        ingest,
        "build_connector",
        lambda source, s=None, transport=None, **kw: FakeConnector(settings, tenders=tenders),
    )
    await ingest.run_fetch(["ted"], days_back=1, settings=settings)
    run = patched_session.execute(select(FetchRun)).scalar_one()
    assert run.status == "partial"
    assert run.records_received == 2
    assert run.records_created == 1
    assert run.records_skipped == 1


async def test_duplicate_concurrent_runs_are_prevented(patched_session, settings, monkeypatch):
    import asyncio

    started = asyncio.Event()
    release = asyncio.Event()

    class SlowConnector(FakeConnector):
        async def fetch(self, date_from, date_to):
            started.set()
            await release.wait()
            return [make_tender()]

    monkeypatch.setattr(
        ingest, "build_connector", lambda source, s=None, transport=None, **kw: SlowConnector(settings)
    )
    first = asyncio.create_task(ingest.run_fetch(["ted"], days_back=1, settings=settings))
    await started.wait()
    assert ingest.running_sources() == {"ted"}

    second = await ingest.run_fetch(["ted"], days_back=1, settings=settings)
    assert second == [], "a source already running must not be started twice"

    release.set()
    assert len(await first) == 1
    assert ingest.running_sources() == set()


async def test_start_fetch_returns_immediately_with_run_ids(patched_session, settings, monkeypatch):
    import asyncio

    monkeypatch.setattr(
        ingest,
        "build_connector",
        lambda source, s=None, transport=None, **kw: FakeConnector(settings, tenders=[make_tender()]),
    )
    response = await ingest.start_fetch(["ted"], days_back=1, settings=settings)
    assert response["run_ids"]
    assert response["runs"][0]["status"] == "queued"
    assert response["skipped_sources"] == []

    run_id = response["run_ids"][0]
    for _ in range(200):
        await asyncio.sleep(0.01)
        patched_session.expire_all()
        run = patched_session.get(FetchRun, run_id)
        if run.status not in ("queued", "running"):
            break
    assert run.status == "success"
    assert run.records_created == 1
