"""Operator-editable matching rules.

The YAML file is never rewritten. Overrides live in app_settings and are merged
over the file at load, on the same rail as every other operator decision, so
the file's matching contract and its comments survive intact.
"""

from __future__ import annotations

import pytest

from app.services.matching_rules import (
    InvalidRules,
    apply_overrides,
    clear_overrides,
    normalise_phrase,
    read_rules,
    save_overrides,
)


def test_reading_returns_the_curated_subset(db_session):
    rules = read_rules(db_session)
    assert set(rules["weights"]) == {"topic", "product_fit", "procurement_intent"}
    assert "good_fit" in rules["bands"]
    assert any(p["key"] == "sds_management" for p in rules["profiles"])


def test_weights_must_sum_to_one(db_session):
    with pytest.raises(InvalidRules) as exc:
        save_overrides(db_session, {"weights": {"topic": 0.5, "product_fit": 0.3, "procurement_intent": 0.3}})
    assert "1.00" in str(exc.value)


def test_weights_summing_to_one_are_stored(db_session):
    save_overrides(db_session, {"weights": {"topic": 0.6, "product_fit": 0.25, "procurement_intent": 0.15}})
    assert read_rules(db_session)["weights"]["topic"] == 0.6


def test_a_stored_override_wins_over_the_file(db_session):
    save_overrides(db_session, {"bands": {"good_fit": 80}})
    merged = apply_overrides(db_session, {"bands": {"good_fit": 70, "excellent_fit": 85}, "weights": {}})
    assert merged["bands"]["good_fit"] == 80
    # Everything not overridden is left exactly as the file had it.
    assert merged["bands"]["excellent_fit"] == 85


def test_clearing_overrides_restores_the_file(db_session):
    save_overrides(db_session, {"bands": {"good_fit": 80}})
    clear_overrides(db_session)
    merged = apply_overrides(db_session, {"bands": {"good_fit": 70}, "weights": {}})
    assert merged["bands"]["good_fit"] == 70


def test_bands_must_stay_in_order(db_session):
    # A good_fit above excellent_fit would put a tender in two bands at once.
    with pytest.raises(InvalidRules):
        save_overrides(db_session, {"bands": {"good_fit": 90, "excellent_fit": 85}})


def test_a_band_outside_the_score_range_is_refused(db_session):
    with pytest.raises(InvalidRules):
        save_overrides(db_session, {"bands": {"good_fit": 140}})


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Cloud-Based Platform", "cloud based platform"),
        ("  SDS   management  ", "sds management"),
        ("données de sécurité", "donnees de securite"),
        ("buyer's data centre", "buyer s data centre"),
    ],
)
def test_phrases_normalise_to_the_files_matching_contract(raw, expected):
    # The file documents this contract in a comment; the UI enforces it, so the
    # trap it warns about stops existing.
    assert normalise_phrase(raw) == expected


def test_phrases_are_normalised_before_they_are_stored(db_session):
    save_overrides(db_session, {"profiles": {"sds_management": {"strong": ["Cloud-Based SDS"]}}})
    rules = read_rules(db_session)
    profile = next(p for p in rules["profiles"] if p["key"] == "sds_management")
    assert "cloud based sds" in profile["strong"]


def test_an_unknown_profile_is_refused(db_session):
    with pytest.raises(InvalidRules):
        save_overrides(db_session, {"profiles": {"not_a_profile": {"strong": ["x"]}}})


# --- endpoints and effect on scoring ---------------------------------------


def test_get_returns_the_rules(client):
    body = client.get("/api/matching-rules").json()
    assert set(body["weights"]) == {"topic", "product_fit", "procurement_intent"}
    assert body["overridden"] == []


def test_put_refuses_weights_that_do_not_sum_to_one(client):
    response = client.put(
        "/api/matching-rules",
        json={"weights": {"topic": 0.9, "product_fit": 0.9, "procurement_intent": 0.9}},
    )
    assert response.status_code == 422
    assert "1.00" in response.json()["detail"]


def test_put_stores_and_reports_what_was_rescored(client):
    response = client.put("/api/matching-rules", json={"bands": {"good_fit": 75}})
    assert response.status_code == 200
    assert "rescored" in response.json()
    assert client.get("/api/matching-rules").json()["bands"]["good_fit"] == 75
    assert client.get("/api/matching-rules").json()["overridden"] == ["bands"]


def test_delete_hands_control_back_to_the_file(client):
    client.put("/api/matching-rules", json={"bands": {"good_fit": 75}})
    assert client.delete("/api/matching-rules").status_code == 200
    assert client.get("/api/matching-rules").json()["overridden"] == []


def test_a_changed_band_actually_moves_the_scoring_engine(client, db_session):
    """The point of the whole feature: a saved rule has to reach the engine."""
    from app.services.matching_rules import engine_for

    before = engine_for(db_session).bands["good_fit"]
    client.put("/api/matching-rules", json={"bands": {"good_fit": before + 5}})
    assert engine_for(db_session).bands["good_fit"] == before + 5


def test_preview_reports_what_would_move_without_moving_it(client, db_session):
    from app.models import Tender

    before = [(t.id, t.relevance_score) for t in db_session.query(Tender).all()]
    body = client.post("/api/matching-rules/preview", json={"bands": {"good_fit": 60}}).json()
    assert set(body) >= {"changed", "crossing_up", "crossing_down", "examined", "sampled"}
    after = [(t.id, t.relevance_score) for t in db_session.query(Tender).all()]
    assert before == after, "preview must not rewrite any stored score"


def test_preview_refuses_invalid_rules_like_save_does(client):
    response = client.post(
        "/api/matching-rules/preview",
        json={"weights": {"topic": 0.1, "product_fit": 0.1, "procurement_intent": 0.1}},
    )
    assert response.status_code == 422


def test_an_empty_corpus_previews_as_no_change(client):
    response = client.post("/api/matching-rules/preview", json={"bands": {"good_fit": 60}})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["changed"] == 0
    assert body["examined"] == 0
    assert body["sampled"] is False


def test_preview_scores_a_real_corpus(client, db_session):
    """A preview over an empty database proves nothing: the loop never runs.

    Regression — the first version of preview() read `result.score`, which does
    not exist on RelevanceResult, and every test passed because none of them
    had a tender to score.
    """
    from app.models import Tender
    from app.services.ingest import _apply_score
    from app.services.matching_rules import engine_for

    row = Tender(
        source="ted",
        source_notice_id="PREVIEW-1",
        content_hash="h",
        title="Cloud-hosted safety data sheet management platform",
        description="SDS authoring and distribution, GHS compliance.",
    )
    _apply_score(row, engine_for(db_session))
    db_session.add(row)
    db_session.commit()

    response = client.post("/api/matching-rules/preview", json={"bands": {"good_fit": 60}})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["examined"] == 1
    assert body["total"] == 1


# --- profiles as data ------------------------------------------------------


def test_a_new_profile_can_be_added(db_session):
    save_overrides(
        db_session,
        {"profiles": {"waste_management": {"label": "Waste management", "strong": ["waste tracking"]}}},
    )
    rules = read_rules(db_session)
    added = next(p for p in rules["profiles"] if p["key"] == "waste_management")
    assert added["label"] == "Waste management"
    assert added["strong"] == ["waste tracking"]
    # The file's own profiles are untouched by adding one.
    assert any(p["key"] == "sds_management" for p in rules["profiles"])


def test_a_new_profile_reaches_the_merged_config(db_session):
    save_overrides(
        db_session,
        {"profiles": {"waste_management": {"label": "Waste management", "strong": ["waste tracking"]}}},
    )
    merged = apply_overrides(db_session, {"profiles": {}, "weights": {}})
    assert "waste_management" in merged["profiles"]


def test_a_profile_from_the_file_can_be_removed(db_session):
    before = {p["key"] for p in read_rules(db_session)["profiles"]}
    assert "sds_distribution" in before

    save_overrides(db_session, {"removed_profiles": ["sds_distribution"]})
    after = {p["key"] for p in read_rules(db_session)["profiles"]}
    assert "sds_distribution" not in after
    assert "sds_management" in after, "removing one leaves the rest alone"


def test_removing_a_profile_takes_it_out_of_the_merged_config(db_session):
    save_overrides(db_session, {"removed_profiles": ["sds_distribution"]})
    merged = apply_overrides(db_session, {"profiles": {}, "weights": {}})
    assert "sds_distribution" not in merged["profiles"]


def test_a_removal_can_be_undone(db_session):
    # The file is never rewritten, so a removal is a tombstone and lifting it
    # brings the original back exactly as the file has it.
    save_overrides(db_session, {"removed_profiles": ["sds_distribution"]})
    save_overrides(db_session, {"removed_profiles": []})
    assert "sds_distribution" in {p["key"] for p in read_rules(db_session)["profiles"]}


def test_a_profile_needs_a_usable_key(db_session):
    with pytest.raises(InvalidRules):
        save_overrides(db_session, {"profiles": {"Not A Key!": {"label": "x"}}})


def test_a_new_profile_needs_a_label(db_session):
    with pytest.raises(InvalidRules):
        save_overrides(db_session, {"profiles": {"waste_management": {"strong": ["x"]}}})


def test_phrases_in_a_new_profile_are_normalised_too(db_session):
    save_overrides(
        db_session,
        {"profiles": {"waste_mgmt": {"label": "Waste", "strong": ["Waste-Tracking Platform"]}}},
    )
    added = next(p for p in read_rules(db_session)["profiles"] if p["key"] == "waste_mgmt")
    assert added["strong"] == ["waste tracking platform"]
