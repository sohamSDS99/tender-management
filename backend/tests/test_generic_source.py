"""Sources described by data rather than programmed.

The mapping layer is what makes an unanticipated portal usable, so its edges
are the important part: a path into an array, a missing required field, and a
payload that parses to nothing at all.
"""

from __future__ import annotations

import pytest

from app.connectors.generic import (
    MappingError,
    apply_mapping,
    describe_paths,
    extract_path,
    records_from,
)

PAYLOAD = {
    "meta": {"total": 2},
    "data": {
        "items": [
            {
                "id": "N-1",
                "notice": {"subject": "Supply of laboratory chemicals"},
                "dates": {"closing": "2026-09-30T17:00:00Z"},
                "organisation": {"legalName": "Ministry of Health"},
                "links": {"self": "https://example.gov/notice/N-1"},
            },
            {
                "id": "N-2",
                "notice": {"subject": "SDS management platform"},
                "dates": {"closing": "2026-10-15"},
                "organisation": {"legalName": "Federal Agency"},
                "links": {"self": "https://example.gov/notice/N-2"},
            },
        ]
    },
}

MAPPING = {
    "records": "data.items[]",
    "source_notice_id": "id",
    "title": "notice.subject",
    "deadline": "dates.closing",
    "buyer_name": "organisation.legalName",
    "source_url": "links.self",
}


class TestExtractPath:
    def test_reads_a_nested_value(self):
        assert extract_path(PAYLOAD, "meta.total") == 2

    def test_a_missing_path_is_none_not_an_error(self):
        # A portal that omits an optional field on some notices must not fail
        # the whole sweep.
        assert extract_path(PAYLOAD, "meta.nothing.here") is None

    def test_indexes_into_a_list(self):
        assert extract_path(PAYLOAD, "data.items[].id") == ["N-1", "N-2"]

    def test_an_empty_path_yields_nothing(self):
        assert extract_path(PAYLOAD, "") is None


class TestRecordsFrom:
    def test_finds_the_record_array(self):
        assert len(records_from(PAYLOAD, "data.items[]")) == 2

    def test_a_path_to_a_non_list_yields_no_records(self):
        assert records_from(PAYLOAD, "meta.total") == []


class TestApplyMapping:
    def test_maps_a_record_onto_the_contract(self):
        tender = apply_mapping(PAYLOAD["data"]["items"][0], MAPPING, source="acme")
        assert tender.source == "acme"
        assert tender.source_notice_id == "N-1"
        assert tender.title == "Supply of laboratory chemicals"
        assert tender.buyer_name == "Ministry of Health"
        assert tender.source_url == "https://example.gov/notice/N-1"
        assert tender.deadline is not None

    def test_parses_a_date_only_deadline(self):
        tender = apply_mapping(PAYLOAD["data"]["items"][1], MAPPING, source="acme")
        assert tender.deadline is not None
        assert tender.deadline.year == 2026

    def test_a_record_without_an_id_is_refused(self):
        # Without it every sweep re-inserts the same notice: source_notice_id is
        # half the dedupe key.
        with pytest.raises(MappingError):
            apply_mapping({"notice": {"subject": "x"}}, MAPPING, source="acme")

    def test_a_record_without_a_title_is_refused(self):
        with pytest.raises(MappingError):
            apply_mapping({"id": "N-9"}, MAPPING, source="acme")

    def test_an_absent_optional_field_is_simply_absent(self):
        record = {"id": "N-3", "notice": {"subject": "Something"}}
        tender = apply_mapping(record, MAPPING, source="acme")
        assert tender.deadline is None
        assert tender.buyer_name is None

    def test_the_whole_record_is_kept_as_the_raw_payload(self):
        # The mapping may be wrong. Keeping the original is what makes a
        # mis-mapped source diagnosable instead of merely broken.
        tender = apply_mapping(PAYLOAD["data"]["items"][0], MAPPING, source="acme")
        assert tender.raw_payload["id"] == "N-1"


class TestDescribePaths:
    def test_reports_paths_with_a_sample_value(self):
        found = dict(describe_paths(PAYLOAD["data"]["items"][0]))
        assert found["notice.subject"] == "Supply of laboratory chemicals"
        assert found["id"] == "N-1"

    def test_walks_into_arrays_once(self):
        paths = dict(describe_paths(PAYLOAD))
        assert "data.items[].id" in paths

    def test_stops_before_a_pathological_depth(self):
        deep: dict = {}
        node = deep
        for _ in range(50):
            node["down"] = {}
            node = node["down"]
        node["leaf"] = "bottom"
        paths = dict(describe_paths(deep))
        assert all(p.count(".") <= 8 for p in paths)


# --- the connector, and the registry it has to join ------------------------


def _row(**kw):
    from app.models import Source

    defaults = dict(
        name="acme",
        display_name="Acme Tenders",
        homepage="https://example.gov",
        url="https://example.gov/api/notices",
        auth="query",
        auth_param="api_key",
        format="json",
        mapping=MAPPING,
        enabled=True,
        notes="",
    )
    defaults.update(kw)
    return Source(**defaults)


def test_a_source_needing_a_key_is_unavailable_without_one(settings):
    from app.connectors.generic import GenericConnector

    connector = GenericConnector(_row(), settings, credential=None)
    assert connector.unavailable_reason() is not None
    assert GenericConnector(_row(), settings, credential="K").unavailable_reason() is None


def test_a_source_needing_no_key_is_available(settings):
    from app.connectors.generic import GenericConnector

    connector = GenericConnector(_row(auth="none"), settings, credential=None)
    assert connector.unavailable_reason() is None


def test_the_credential_goes_where_the_row_says(settings):
    from app.connectors.generic import GenericConnector

    q = GenericConnector(_row(auth="query", auth_param="key"), settings, credential="SECRET")
    assert q._auth() == ({"key": "SECRET"}, {})

    h = GenericConnector(_row(auth="header", auth_param="X-Token"), settings, credential="SECRET")
    assert h._auth() == ({}, {"X-Token": "SECRET"})

    b = GenericConnector(_row(auth="bearer"), settings, credential="SECRET")
    assert b._auth() == ({}, {"Authorization": "Bearer SECRET"})


def test_parsing_a_payload_yields_tenders(settings, monkeypatch):
    from app.connectors import generic

    # The prefilter is a topical filter against the company profile; this test
    # is about mapping, so let everything through.
    monkeypatch.setattr(generic.GenericConnector, "keep", lambda self, *a: True)
    tenders = generic.GenericConnector(_row(), settings, credential="K").parse(PAYLOAD)
    assert [t.source_notice_id for t in tenders] == ["N-1", "N-2"]
    assert all(t.source == "acme" for t in tenders)


def test_one_unmappable_record_does_not_lose_the_batch(settings, monkeypatch):
    from app.connectors import generic

    monkeypatch.setattr(generic.GenericConnector, "keep", lambda self, *a: True)
    payload = {"data": {"items": [{"id": "N-1", "notice": {"subject": "Fine"}}, {"nope": True}]}}
    tenders = generic.GenericConnector(_row(), settings, credential="K").parse(payload)
    assert len(tenders) == 1


def test_a_user_added_source_joins_the_registry(db_session, settings):
    from app.connectors.registry import build_all, enabled_sources

    db_session.add(_row())
    db_session.commit()

    names = [c.source_name for c in build_all(settings, db=db_session)]
    assert "acme" in names
    assert "ted" in names, "the built-ins are unaffected"

    # Still unavailable until a key is stored, so it must not join a sweep yet.
    assert "acme" not in enabled_sources(settings, db=db_session)

    from app.services.credentials import set_credential

    set_credential(db_session, "acme", "A-KEY")
    assert "acme" in enabled_sources(settings, db=db_session)


def test_a_disabled_row_never_joins_a_sweep(db_session, settings):
    from app.connectors.registry import enabled_sources
    from app.services.credentials import set_credential

    db_session.add(_row(enabled=False))
    db_session.commit()
    set_credential(db_session, "acme", "A-KEY")
    assert "acme" not in enabled_sources(settings, db=db_session)
