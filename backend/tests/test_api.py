"""API: filtering, pagination, detail, fetch control, stats."""

from __future__ import annotations

from datetime import timedelta

import pytest

from app.models import FetchRun, utcnow
from app.services import ingest

# Share test_ingest's frozen instant rather than reading the wall clock.
# make_tender() dates its fixtures relative to that constant, so a live utcnow()
# here drifts away from them and the date-window assertions below start failing
# once real time passes it - a time bomb that fired on 2026-08-22.
from tests.test_ingest import NOW, make_tender

#: Deadlines are dated from the *real* clock. Publication dates are not, and the
#: asymmetry is the whole fix rather than an inconsistency.
#:
#: Whether a notice is open is judged twice against the wall clock, by production
#: code this suite does not control: the scoring engine applies an expired
#: multiplier and drops `fit_status`, and the `active_only` SQL clause compares
#: against `utcnow()`. So a deadline pinned to a frozen instant is guaranteed to
#: expire eventually - `review-1` was `NOW + 5 days`, and on 2026-08-27 four
#: tests began failing because a notice this fixture calls "open" had been in the
#: past for six days. Moving it is no fix either: `test_date_window_filters`
#: requires it inside `NOW + 10 days`, and the wall clock passes any literal.
#:
#: Publication dates have no second reader - nothing in production compares them
#: to now - so they stay on `NOW` and keep the `published_from` assertions exact.
#:
#: The rule this encodes: **freeze a date only the test reads; date a field the
#: production code judges against the wall clock from the wall clock.** Offsets
#: below are unchanged, so every ordering and window assertion still holds.
DEADLINE_BASE = utcnow()


@pytest.fixture
def seeded(db_session):
    rows = [
        make_tender(
            source="ted",
            source_notice_id="high-1",
            title="Cloud hosted SDS management and chemical inventory platform",
            buyer_country="DEU",
            deadline=DEADLINE_BASE + timedelta(days=20),
        ),
        make_tender(
            source="find_a_tender",
            source_notice_id="review-1",
            title="Occupational health and safety management system software",
            description="Suppliers may propose a cloud or on premises deployment; an optional "
            "locally hosted deployment is acceptable. Incident management and audit management.",
            buyer_country="GB",
            deadline=DEADLINE_BASE + timedelta(days=5),
        ),
        make_tender(
            source="sam",
            source_notice_id="onprem-1",
            title="EHS incident management system",
            description="Chemical inventory and incident management. All application components "
            "must reside on the buyer's network and cloud hosting is prohibited.",
            buyer_country="US",
            status="open",
            deadline=DEADLINE_BASE + timedelta(days=40),
        ),
        make_tender(
            source="austender",
            source_notice_id="ppe-1",
            title="Supply of personal protective equipment",
            description="Purchase of safety boots and protective clothing.",
            buyer_country="AU",
            deadline=DEADLINE_BASE - timedelta(days=2),
            status="closed",
        ),
    ]
    ingest.store_tenders(db_session, rows)
    db_session.add(
        FetchRun(
            source="ted",
            status="success",
            started_at=NOW - timedelta(hours=2),
            finished_at=NOW - timedelta(hours=1),
            records_received=4,
            records_created=4,
        )
    )
    db_session.add(
        FetchRun(
            source="pncp",
            status="failed",
            started_at=NOW - timedelta(hours=2),
            finished_at=NOW - timedelta(hours=2),
            error_message="transport error: ReadTimeout",
        )
    )
    db_session.commit()
    return db_session


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_openapi_documents_every_endpoint(client):
    paths = client.get("/openapi.json").json()["paths"]
    assert {
        "/health",
        "/api/sources",
        "/api/tenders",
        "/api/tenders/{tender_id}",
        "/api/fetch",
        "/api/fetch-runs",
        "/api/tenders/rescore",
        "/api/stats",
    } <= set(paths)


def test_sources_report_status_and_key_requirements(client, seeded):
    entries = {e["name"]: e for e in client.get("/api/sources").json()}
    assert len(entries) == 8
    # The bulk extract is the default transport, so SAM needs no credential and
    # is available without one. tests/test_connectors.py covers the API path,
    # where a missing key does still disable it.
    assert entries["sam"]["requires_api_key"] is False
    assert entries["sam"]["unavailable_reason"] is None
    assert entries["ted"]["tender_count"] == 1
    assert entries["ted"]["last_status"] == "success"
    assert entries["ted"]["last_success_at"] is not None
    assert entries["pncp"]["last_status"] == "failed"
    assert entries["pncp"]["last_error"].startswith("transport error")


def test_tender_list_is_scored_sorted_and_paginated(client, seeded):
    payload = client.get("/api/tenders?page_size=2").json()
    assert payload["total"] == 4
    assert payload["pages"] == 2
    assert len(payload["items"]) == 2
    scores = [item["relevance_score"] for item in payload["items"]]
    assert scores == sorted(scores, reverse=True)

    page2 = client.get("/api/tenders?page_size=2&page=2").json()
    assert page2["page"] == 2
    assert {i["id"] for i in page2["items"]}.isdisjoint({i["id"] for i in payload["items"]})


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("query=chemical inventory", {"high-1", "onprem-1"}),
        ("minimum_score=70", {"high-1"}),
        ("maximum_score=25", {"onprem-1", "ppe-1"}),
        ("sources=ted", {"high-1"}),
        ("sources=ted&sources=sam", {"high-1", "onprem-1"}),
        ("countries=GB", {"review-1"}),
        ("fit_statuses=manual_review", {"review-1"}),
        ("fit_statuses=not_fit", {"onprem-1", "ppe-1"}),
        ("deployment_fits=mandatory_on_premises", {"onprem-1"}),
        ("deployment_fits=hybrid", {"review-1"}),
        ("categories=sds_management", {"high-1"}),
        ("statuses=closed", {"ppe-1"}),
        ("active_only=true&minimum_score=1", {"high-1", "review-1", "onprem-1"}),
        ("has_deadline=true&minimum_score=1", {"high-1", "review-1", "onprem-1", "ppe-1"}),
    ],
)
def test_tender_filters(client, seeded, query, expected):
    items = client.get(f"/api/tenders?{query}&page_size=50").json()["items"]
    assert {item["source_notice_id"] for item in items} == expected


def test_date_window_filters(client, seeded):
    soon = (DEADLINE_BASE + timedelta(days=10)).isoformat()
    items = client.get(f"/api/tenders?deadline_to={soon}&page_size=50").json()["items"]
    assert {i["source_notice_id"] for i in items} == {"review-1", "ppe-1"}

    published_from = (NOW - timedelta(days=2)).isoformat()
    assert client.get(f"/api/tenders?published_from={published_from}").json()["total"] == 4
    future = (NOW + timedelta(days=1)).isoformat()
    assert client.get(f"/api/tenders?published_from={future}").json()["total"] == 0


@pytest.mark.parametrize(
    "sort",
    [
        "score_desc",
        "score_asc",
        "deadline_asc",
        "deadline_desc",
        "published_desc",
        "published_asc",
        "first_seen_desc",
    ],
)
def test_every_sort_option_works(client, seeded, sort):
    payload = client.get(f"/api/tenders?sort={sort}").json()
    assert payload["total"] == 4
    assert len(payload["items"]) == 4


def test_deadline_ascending_puts_the_earliest_first(client, seeded):
    items = client.get("/api/tenders?sort=deadline_asc&has_deadline=true").json()["items"]
    deadlines = [i["deadline"] for i in items]
    assert deadlines == sorted(deadlines)


def test_tender_detail_exposes_explanation_and_raw_payload(client, seeded):
    listed = client.get("/api/tenders?minimum_score=70").json()["items"][0]
    detail = client.get(f"/api/tenders/{listed['id']}").json()
    assert detail["description"]
    assert detail["relevance_reasons"]
    assert detail["classification_codes"] == [{"scheme": "CPV", "code": "48000000"}]
    assert detail["document_urls"]
    assert detail["raw_payload"]["publication-number"] == "1-2026"
    assert detail["topic_relevance_score"] > 0
    assert detail["content_hash"]
    assert detail["deadline"].endswith("Z"), "datetimes are serialized as UTC"


def test_unknown_tender_is_404(client):
    assert client.get("/api/tenders/999999").status_code == 404


def test_disqualified_tender_is_still_visible_with_its_reasons(client, seeded):
    items = client.get("/api/tenders?deployment_fits=mandatory_on_premises").json()["items"]
    assert len(items) == 1
    assert items[0]["relevance_score"] <= 20
    assert items[0]["fit_status"] == "not_fit"
    assert items[0]["disqualifiers"]
    assert items[0]["relevance_reasons"], "the topic reasons stay visible for verification"


def test_fetch_runs_listing_and_filters(client, seeded):
    runs = client.get("/api/fetch-runs").json()
    assert len(runs) == 2
    assert client.get("/api/fetch-runs?source=pncp").json()[0]["status"] == "failed"
    assert client.get("/api/fetch-runs?status=success").json()[0]["source"] == "ted"


def test_trigger_fetch_returns_run_ids_without_waiting(client, monkeypatch):
    calls: list[dict] = []

    # Mirrors the real signature, batch_id included: a double that accepts less
    # than the caller passes turns a route change into a confusing TypeError
    # instead of a clear assertion failure.
    async def fake_start(sources=None, days_back=None, trigger="manual", settings=None, batch_id=None):
        calls.append({"sources": sources, "days_back": days_back, "trigger": trigger, "batch_id": batch_id})
        return {
            "runs": [{"id": 1, "source": "ted", "status": "queued"}],
            "run_ids": [1],
            "skipped_sources": ["sam"],
            "window_from": NOW - timedelta(days=3),
            "window_to": NOW,
        }

    monkeypatch.setattr(ingest, "start_fetch", fake_start)
    response = client.post("/api/fetch", json={"sources": ["ted"], "days_back": 3})
    assert response.status_code == 202
    assert response.json()["run_ids"] == [1]
    assert response.json()["skipped_sources"] == ["sam"]
    assert len(calls) == 1
    assert calls[0]["sources"] == ["ted"]
    assert calls[0]["days_back"] == 3
    assert calls[0]["trigger"] == "manual"
    # Every sweep is attributable, so /api/automation can report on this one.
    assert calls[0]["batch_id"]


def test_trigger_fetch_rejects_unknown_sources(client):
    response = client.post("/api/fetch", json={"sources": ["ted", "nope"]})
    assert response.status_code == 400
    assert "nope" in response.json()["detail"]


def test_rescore_endpoint(client, seeded):
    response = client.post("/api/tenders/rescore")
    assert response.status_code == 200
    assert response.json()["rescored"] == 4


def test_stats_summarise_the_dashboard(client, seeded):
    stats = client.get("/api/stats").json()
    assert stats["total_tenders"] == 4
    assert stats["good_fit_or_better"] >= 1
    assert stats["not_relevant"] >= 2
    assert stats["actionable"] == 3
    assert stats["failed_sources"] == 1
    assert stats["last_successful_fetch"]
    assert {b["key"] for b in stats["by_source"]} == {"ted", "find_a_tender", "sam", "austender"}
    assert {b["key"] for b in stats["by_fit_status"]} <= {
        "high_fit",
        "good_fit",
        "possible_fit",
        "manual_review",
        "not_fit",
    }
    assert "GB" in stats["countries"]
    assert stats["score_bands"]["excellent_fit"] == 85
    assert len(stats["categories"]) == 8


def test_first_seen_from_filters_on_discovery_time(client, seeded, db_session) -> None:
    """The "what arrived recently" filter behind the New view.

    first_seen_at is written once at insert and never touched again, so this
    selects by when we discovered a notice - not when the buyer amended it.
    """
    from app.models import Tender

    rows = db_session.query(Tender).order_by(Tender.id).all()
    old, recent = rows[0], rows[1]
    old.first_seen_at = NOW - timedelta(days=10)
    recent.first_seen_at = NOW - timedelta(hours=2)
    db_session.commit()

    cutoff = (NOW - timedelta(days=1)).isoformat()
    items = client.get(f"/api/tenders?minimum_score=0&first_seen_from={cutoff}&page_size=50").json()["items"]
    ids = {i["id"] for i in items}
    assert recent.id in ids
    assert old.id not in ids


def test_first_seen_from_is_optional(client, seeded) -> None:
    """Omitting it must not narrow anything."""
    everything = client.get("/api/tenders?minimum_score=0&page_size=50").json()["total"]
    assert client.get("/api/tenders?minimum_score=0&page_size=50").json()["total"] == everything
