"""Reviewer verdicts, the patterns learned from them, and what stays untouched.

Every notice here is written straight into the database rather than through a
connector: what is under test is the learner and the filter, and the ingest path
has its own file. Deadlines are absent or years out on purpose - the suite must
not depend on the day it is run (see the wall-clock note in CLAUDE.md).
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from app.models import Tender, TenderFeedback
from app.schemas import TenderListItem
from app.services import feedback, ingest, notifier

NOW = datetime(2026, 8, 21, 12, 0)

#: Six notices nobody wants, sharing a vocabulary. Enough to clear MIN_MARKS,
#: and each phrase appears in all six so it clears MIN_DOC_FREQ too.
REJECTS = [
    "Supply of laboratory furniture for the university campus",
    "Supply and installation of laboratory furniture, phase two",
    "Laboratory furniture and fume cupboards for the university",
    "Refurbishment of laboratory furniture at the campus annexe",
    "Laboratory furniture: benches, stools and storage for the university",
    "Tender for laboratory furniture supply to the campus estate",
]

#: What the tool is actually for. No word above appears in any of them.
KEEPERS = [
    "Cloud hosted safety data sheet management platform",
    "SaaS chemical inventory and GHS labelling system",
    "Software as a service EHS incident reporting solution",
    "Hosted SDS authoring and distribution subscription",
    "Chemical compliance software with REACH reporting",
    "Cloud platform for occupational health and safety management",
    "Subscription software for hazardous substance registers",
    "Web based safety data sheet distribution service",
    "Chemical inventory software as a service renewal",
    "Cloud EHS audit and inspection management software",
]

#: Background bulk, and it has to be here.
#:
#: The log-odds compare rejections against the *rest of the corpus*, so the rest
#: must be large enough for a ratio to mean anything. In a seventeen-notice
#: fixture a single unmarked notice sharing a phrase drops that phrase from
#: 6-in-6 versus 0-in-11 to 6-in-6 versus 1-in-11, which is genuinely weak
#: evidence and correctly falls under STRONG_AT - an artefact of the fixture's
#: size, not of the learner. Production has hundreds of notices; forty is enough
#: to stop the fixture lying about the maths.
FILLER = [f"Municipal grounds maintenance and landscaping contract, lot {i}" for i in range(30)]

#: One name for the fixture's size, so a count assertion cannot drift from it.
CORPUS_SIZE = len(REJECTS) + len(KEEPERS) + len(FILLER)


def add(db, notice_id: str, title: str, **overrides) -> Tender:
    """One stored notice. 'contract' rides on every description so the tests can
    show that a word common to both halves earns no weight at all."""
    row = Tender(
        source="ted",
        source_notice_id=notice_id,
        content_hash=f"hash-{notice_id}",
        title=title,
        description="Framework contract. Award under open procedure.",
        buyer_name="Some Public Body",
        first_seen_at=NOW,
        last_seen_at=NOW,
        **overrides,
    )
    db.add(row)
    db.commit()
    return row


@pytest.fixture
def corpus(db_session):
    """Forty-six notices, none marked yet."""
    for index, title in enumerate(REJECTS):
        add(db_session, f"reject-{index}", title)
    for index, title in enumerate(KEEPERS):
        add(db_session, f"keep-{index}", title)
    for index, title in enumerate(FILLER):
        add(db_session, f"filler-{index}", title)
    return db_session


def reject_all(db) -> None:
    """Mark all six, then apply what that teaches."""
    for index in range(len(REJECTS)):
        row = db.execute(select(Tender).where(Tender.source_notice_id == f"reject-{index}")).scalar_one()
        feedback.set_verdict(db, row.id, "irrelevant")
    feedback.apply_to_corpus(db)


def by_notice(db, notice_id: str) -> Tender:
    return db.execute(select(Tender).where(Tender.source_notice_id == notice_id)).scalar_one()


# --- tokenising ------------------------------------------------------------


def test_tokens_are_word_like_unigrams_and_pairs():
    bag = feedback.tokens("Laboratory furniture, ref 2026/S 12-9")
    assert "laboratory" in bag
    assert "laboratory furniture" in bag, "bigrams are what read as a pattern"
    assert "s" not in bag, "a single letter carries nothing transferable"
    assert "2026" not in bag, "a bare number is a reference, not a meaning"


def test_tokens_fold_accents_like_the_scoring_engine_does():
    # Same normaliser as relevance.py, deliberately: a learned pattern and a
    # configured phrase have to mean the same thing.
    assert "mobilier" in feedback.tokens("Mobilier de laboratoire")
    assert feedback.tokens("Fume Cupboards") == feedback.tokens("fume  cupboards!")


# --- the floors ------------------------------------------------------------


def test_learner_is_silent_below_the_mark_floor(corpus):
    for index in range(feedback.MIN_MARKS - 1):
        feedback.set_verdict(corpus, by_notice(corpus, f"reject-{index}").id, "irrelevant")
    feedback.apply_to_corpus(corpus)

    model = feedback.model_for(corpus)
    assert model.active is False
    assert model.marks_irrelevant == feedback.MIN_MARKS - 1
    # Four rejections is an opinion, not a pattern: nothing may be hidden by it.
    assert corpus.execute(select(Tender).where(Tender.auto_irrelevant.is_(True))).all() == []


def test_learning_hides_notices_that_look_like_the_rejected_ones(corpus):
    reject_all(corpus)
    model = feedback.model_for(corpus)
    assert model.active is True

    fresh = add(corpus, "new-1", "Supply of laboratory furniture to the campus")
    feedback.apply_prediction(fresh, model)
    assert fresh.auto_irrelevant is True
    assert fresh.auto_irrelevant_reasons, "a hidden notice must say what hid it"
    assert any("laboratory furniture" in reason for reason in fresh.auto_irrelevant_reasons)


def test_learning_leaves_the_notices_we_want_alone(corpus):
    reject_all(corpus)
    for index in range(len(KEEPERS)):
        assert by_notice(corpus, f"keep-{index}").auto_irrelevant is False


def test_a_word_common_to_both_halves_earns_no_weight(corpus):
    reject_all(corpus)
    patterns = feedback.model_for(corpus).patterns
    # 'contract' is in every description in this corpus, rejected or not, so the
    # log-odds cancel. This is why there is no stop-word list.
    assert "contract" not in patterns
    assert "laboratory" in patterns


def test_one_notice_cannot_invent_a_pattern(corpus):
    reject_all(corpus)
    patterns = feedback.model_for(corpus).patterns
    # 'annexe' appears in exactly one rejection, so it is under MIN_DOC_FREQ.
    assert "annexe" not in patterns
    assert "cupboards" not in patterns


def test_a_relevant_mark_strikes_its_words_out_of_the_model(corpus):
    add(corpus, "wanted-1", "SDS platform for the university campus")
    feedback.set_verdict(corpus, by_notice(corpus, "wanted-1").id, "relevant")
    reject_all(corpus)

    patterns = feedback.model_for(corpus).patterns
    # A pattern present in something the team said yes to must never be able to
    # hide anything - even though 'university' is in all six rejections.
    assert "university" not in patterns
    assert "campus" not in patterns
    assert "laboratory furniture" in patterns


def test_a_notice_marked_relevant_is_never_auto_hidden(corpus):
    # The worst case for the learner: a notice that reads exactly like the junk
    # but which a human has explicitly kept.
    wanted = add(corpus, "wanted-2", "Laboratory furniture with integrated SDS cabinets")
    reject_all(corpus)
    feedback.set_verdict(corpus, wanted.id, "relevant")
    feedback.apply_to_corpus(corpus)

    assert by_notice(corpus, "wanted-2").auto_irrelevant is False


def test_clearing_a_verdict_brings_the_hidden_back(corpus):
    reject_all(corpus)
    fresh = add(corpus, "new-2", "Laboratory furniture for the campus")
    feedback.apply_to_corpus(corpus)
    assert by_notice(corpus, "new-2").auto_irrelevant is True

    # Drop below the floor again: the patterns lose their support, so the
    # prediction has to be withdrawn rather than left standing.
    for index in range(2):
        feedback.clear_verdict(corpus, by_notice(corpus, f"reject-{index}").id)
    feedback.apply_to_corpus(corpus)

    assert feedback.model_for(corpus).active is False
    assert by_notice(corpus, "new-2").auto_irrelevant is False
    assert fresh.id is not None


# --- what feedback must never touch ---------------------------------------


def test_marking_never_moves_a_relevance_score(corpus):
    before = {
        row.source_notice_id: (row.relevance_score, row.fit_status, row.relevance_reasons)
        for row in corpus.execute(select(Tender)).scalars()
    }
    reject_all(corpus)
    after = {
        row.source_notice_id: (row.relevance_score, row.fit_status, row.relevance_reasons)
        for row in corpus.execute(select(Tender)).scalars()
    }
    assert before == after, "the engine's arithmetic is frozen; feedback only hides"


def test_a_verdict_survives_a_rescore(corpus):
    reject_all(corpus)
    marked = by_notice(corpus, "reject-0")
    ingest.rescore_all(corpus)

    assert corpus.get(TenderFeedback, marked.id) is not None
    assert corpus.get(TenderFeedback, marked.id).verdict == "irrelevant"


def test_a_verdict_survives_the_notice_being_amended(corpus, settings):
    """The whole reason verdicts are not columns on `tenders`."""
    from tests.test_ingest import make_tender

    reject_all(corpus)
    stored = ingest.upsert_tender(corpus, make_tender(source_notice_id="amend-1"))
    assert stored == "created"
    row = by_notice(corpus, "amend-1")
    feedback.set_verdict(corpus, row.id, "irrelevant", note="wrong country")

    # A real amendment: new content, new hash, the full update path.
    outcome = ingest.upsert_tender(
        corpus, make_tender(source_notice_id="amend-1", title="Amended title entirely")
    )
    assert outcome == "updated"
    kept = corpus.get(TenderFeedback, row.id)
    assert kept is not None and kept.verdict == "irrelevant"
    assert kept.note == "wrong country"


def test_a_verdict_whose_notice_was_deleted_counts_for_nothing(corpus):
    """`--seed-reset` deletes tenders with a Core delete(), and SQLite does not
    enforce ON DELETE CASCADE unless foreign keys are switched on - so a verdict
    can outlive its notice. Counted off the verdict table, five such orphans
    would switch the learner on with marks referring to nothing."""
    from sqlalchemy import delete

    reject_all(corpus)
    assert feedback.model_for(corpus).marks_irrelevant == len(REJECTS)

    corpus.execute(delete(Tender).where(Tender.source_notice_id.like("reject-%")))
    corpus.commit()
    feedback.reset_model_cache()

    model = feedback.model_for(corpus)
    assert model.marks_irrelevant == 0, "an orphaned verdict must not count as a mark"
    assert model.active is False
    assert feedback.apply_to_corpus(corpus) >= 0


# --- the API -------------------------------------------------------------


def test_marking_hides_a_notice_from_the_default_view(client, corpus):
    target = by_notice(corpus, "reject-0")
    response = client.post(
        f"/api/tenders/{target.id}/feedback", json={"verdict": "irrelevant", "note": "furniture"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["verdict"] == "irrelevant"
    assert body["learned"]["marks_irrelevant"] == 1

    visible = client.get("/api/tenders?hidden=false&minimum_score=0&page_size=50").json()
    assert target.id not in [item["id"] for item in visible["items"]]

    only_hidden = client.get("/api/tenders?hidden=true&minimum_score=0&page_size=50").json()
    assert [item["id"] for item in only_hidden["items"]] == [target.id]
    assert only_hidden["items"][0]["hidden"] is True
    assert only_hidden["items"][0]["feedback"]["note"] == "furniture"


def test_omitting_the_filter_returns_everything(client, corpus):
    target = by_notice(corpus, "reject-0")
    client.post(f"/api/tenders/{target.id}/feedback", json={"verdict": "irrelevant"})

    everything = client.get("/api/tenders?minimum_score=0&page_size=50").json()
    assert everything["total"] == CORPUS_SIZE
    # Tri-state, not a flag: without this there would be no way to *find* a
    # mistaken mark, which is the one thing an undo needs.


def test_marking_relevant_does_not_hide(client, corpus):
    target = by_notice(corpus, "keep-0")
    client.post(f"/api/tenders/{target.id}/feedback", json={"verdict": "relevant"})

    visible = client.get("/api/tenders?hidden=false&minimum_score=0&page_size=50").json()
    assert target.id in [item["id"] for item in visible["items"]]


def test_the_answer_says_how_many_others_it_reclassified(client, corpus):
    """The learning loop, made visible. A mark that quietly hid rows would read
    as a bug; the count is what makes it read as the feature."""
    ids = [by_notice(corpus, f"reject-{i}").id for i in range(len(REJECTS))]
    add(corpus, "new-3", "Laboratory furniture supply for the university campus")

    last = None
    for tender_id in ids:
        last = client.post(f"/api/tenders/{tender_id}/feedback", json={"verdict": "irrelevant"})
    assert last is not None and last.status_code == 200
    body = last.json()
    assert body["learned"]["active"] is True
    assert body["reclassified"] >= 1
    assert by_notice(corpus, "new-3").auto_irrelevant is True


def test_clearing_a_mark_is_not_an_error_when_there_was_none(client, corpus):
    target = by_notice(corpus, "keep-1")
    response = client.delete(f"/api/tenders/{target.id}/feedback")
    assert response.status_code == 200
    assert response.json()["verdict"] is None


def test_an_unknown_tender_is_404_and_an_unknown_verdict_is_422(client, corpus):
    assert client.post("/api/tenders/999999/feedback", json={"verdict": "irrelevant"}).status_code == 404
    target = by_notice(corpus, "keep-2")
    assert client.post(f"/api/tenders/{target.id}/feedback", json={"verdict": "maybe"}).status_code == 422


def test_feedback_needs_no_secret(anon_client, corpus):
    """D23: a write is constrained by a limit matching its cost, and this one
    spends nothing. Marks are made in bursts while reading a list."""
    target = by_notice(corpus, "reject-1")
    response = anon_client.post(f"/api/tenders/{target.id}/feedback", json={"verdict": "irrelevant"})
    assert response.status_code == 200


def test_learned_endpoint_shows_the_evidence(client, corpus):
    reject_all(corpus)
    body = client.get("/api/feedback/learned").json()
    assert body["active"] is True
    assert body["marks_needed"] == 0
    assert body["hidden_by_hand"] == len(REJECTS)
    top = body["patterns"][0]
    # Nothing opaque: a pattern arrives with the count of rejections it appears
    # in and the count of everything else, so a wrong one can be argued with.
    assert top["marked"] >= feedback.MIN_DOC_FREQ
    assert top["weight"] >= feedback.MIN_WEIGHT
    assert "elsewhere" in top


def test_learned_endpoint_says_how_many_marks_are_still_needed(client, corpus):
    client.post(f"/api/tenders/{by_notice(corpus, 'reject-0').id}/feedback", json={"verdict": "irrelevant"})
    body = client.get("/api/feedback/learned").json()
    assert body["active"] is False
    assert body["marks_needed"] == feedback.MIN_MARKS - 1
    assert body["patterns"] == [] or body["active"] is False


# --- the two definitions of "hidden" must agree ---------------------------


def test_hidden_in_sql_and_hidden_in_the_response_select_the_same_rows(client, corpus):
    """`hidden` is defined twice - once in SQL for the filter, once as a
    computed field for the response - because one runs in the database and the
    other in Python. This is the test that stops them drifting apart."""
    reject_all(corpus)
    add(corpus, "new-4", "Laboratory furniture for the university")
    feedback.apply_to_corpus(corpus)

    from_sql = {
        item["id"]
        for item in client.get("/api/tenders?hidden=true&minimum_score=0&page_size=100").json()["items"]
    }
    from_python = {
        row.id
        for row in corpus.execute(select(Tender)).scalars()
        if TenderListItem.model_validate(row).hidden
    }
    assert from_sql == from_python
    assert from_sql, "the corpus must actually contain hidden rows for this to prove anything"


def test_visible_and_hidden_partition_the_corpus(client, corpus):
    reject_all(corpus)
    total = client.get("/api/tenders?minimum_score=0&page_size=100").json()["total"]
    shown = client.get("/api/tenders?hidden=false&minimum_score=0&page_size=100").json()["total"]
    gone = client.get("/api/tenders?hidden=true&minimum_score=0&page_size=100").json()["total"]
    assert shown + gone == total


# --- counts stay honest ---------------------------------------------------


def test_stats_exclude_hidden_and_report_the_remainder(client, corpus):
    before = client.get("/api/stats").json()
    assert before["hidden_total"] == 0
    assert before["total_tenders"] == CORPUS_SIZE

    reject_all(corpus)
    after = client.get("/api/stats").json()
    # A tab count has to equal the list the tab opens, and the list hides these.
    assert after["hidden_total"] == len(REJECTS)
    assert after["total_tenders"] == before["total_tenders"] - len(REJECTS)
    listed = client.get("/api/tenders?hidden=false&minimum_score=0&page_size=100").json()["total"]
    assert listed == after["total_tenders"]
    assert sum(bucket["count"] for bucket in after["by_source"]) == after["total_tenders"]


# --- Slack ---------------------------------------------------------------


def test_the_digest_never_announces_a_rejected_notice(db_session, settings):
    good = add(
        db_session,
        "loud-1",
        "Cloud SDS management platform",
        relevance_score=90,
        is_actionable=True,
    )
    since = NOW - timedelta(hours=1)
    db_session.query(Tender).update({Tender.first_seen_at: since + timedelta(minutes=1)})
    db_session.commit()

    assert [t.id for t in notifier.qualifying_tenders(db_session, since, settings)] == [good.id]

    feedback.set_verdict(db_session, good.id, "irrelevant")
    assert notifier.qualifying_tenders(db_session, since, settings) == []
    assert notifier.announceable_tenders(db_session, settings, now=since + timedelta(minutes=2)) == []
