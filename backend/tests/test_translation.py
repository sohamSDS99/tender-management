"""On-demand English translation of a foreign-language notice.

Two thirds of the corpus is English and the rest is mostly Brazilian
Portuguese, so the interesting cases are all about *deciding* rather than
translating: the `language` column stores `en`, `eng`, `English`, `pt` and
`French` in production, and every one of those has to resolve the same way here
as it does in the browser's button.

No test in this file reaches the network. `translate` takes an httpx client, and
the provider is the only thing that touches it, so a `MockTransport` proves the
request we would really send without sending it.
"""

from __future__ import annotations

import httpx
import pytest

from app.models import Tender, TenderTranslation
from app.services import translator
from tests.test_ingest import NOW

PT_DESCRIPTION = (
    "O objeto da presente dispensa de licitação é a escolha da proposta mais "
    "vantajosa para a contratação de solução corporativa de armazenamento."
)
EN_TRANSLATION = (
    "The purpose of this bidding exemption is the choice of the most advantageous "
    "proposal for contracting a corporate storage solution."
)


def add_tender(db, notice_id: str, **overrides) -> Tender:
    fields = {
        "source": "pncp",
        "source_notice_id": notice_id,
        "content_hash": f"hash-{notice_id}",
        "title": "Contratação de solução de armazenamento",
        "description": PT_DESCRIPTION,
        "language": "pt",
        "first_seen_at": NOW,
        "last_seen_at": NOW,
    }
    fields.update(overrides)
    row = Tender(**fields)
    db.add(row)
    db.commit()
    return row


def google_free_response(*translated: str) -> httpx.Response:
    """The endpoint's real shape: nested arrays, not an object.

    ``[[[translated, original, ...], ...], null, detected_source, ...]`` - and a
    long chunk comes back as several segments that concatenate with no
    separator, which is why more than one is worth building here.
    """
    segments = [[part, "ignored", None, None, 3] for part in translated]
    return httpx.Response(200, json=[segments, None, "pt", None, None, None, 1.0, []])


# --- deciding whether to offer it at all ------------------------------------


@pytest.mark.parametrize(
    "stored,expected",
    [
        ("en", "en"),
        ("eng", "en"),
        ("English", "en"),
        ("ENGLISH", "en"),
        ("en-GB", "en"),
        ("pt", "pt"),
        ("por", "pt"),
        ("Portuguese", "pt"),
        ("pt-BR", "pt"),
        ("French", "fr"),
        ("fra", "fr"),
        ("de", "de"),
        # Unknown stays unknown. Guessing a source language is how you get a
        # confident translation of the wrong thing.
        ("Klingon", None),
        ("", None),
        (None, None),
    ],
)
def test_every_spelling_this_corpus_stores_normalises(stored, expected):
    """`en`, `eng`, `English`, `pt` and `French` are all real production values."""
    assert translator.normalise_language(stored) == expected


@pytest.mark.parametrize(
    "language,description,expected",
    [
        ("pt", PT_DESCRIPTION, True),
        ("por", PT_DESCRIPTION, True),
        ("French", PT_DESCRIPTION, True),
        # English in any spelling: nothing to do.
        ("en", PT_DESCRIPTION, False),
        ("eng", PT_DESCRIPTION, False),
        ("English", PT_DESCRIPTION, False),
        # Nothing to translate, whatever the language says.
        ("pt", None, False),
        ("pt", "   ", False),
        # An unrecorded language is left alone rather than guessed at.
        (None, PT_DESCRIPTION, False),
        ("Klingon", PT_DESCRIPTION, False),
    ],
)
def test_the_button_is_offered_only_where_it_can_work(language, description, expected):
    assert translator.needs_translation(language, description) is expected


def test_the_detail_response_carries_the_decision(client, db_session):
    """The browser is told whether to render a button; it does not decide.

    A second normaliser in TypeScript would drift from this one the first time a
    feed changed what it puts in `language`.
    """
    foreign = add_tender(db_session, "pt-1")
    english = add_tender(db_session, "en-1", language="eng", description="Cloud storage framework.")

    assert client.get(f"/api/tenders/{foreign.id}").json()["needs_translation"] is True
    assert client.get(f"/api/tenders/{english.id}").json()["needs_translation"] is False


# --- chunking ---------------------------------------------------------------


def test_short_text_is_one_chunk():
    assert translator.chunk_text("Uma frase curta.", limit=100) == ["Uma frase curta."]


def test_long_text_splits_on_sentence_boundaries_never_mid_word():
    text = " ".join(f"Sentença número {n} com algum texto." for n in range(200))
    chunks = translator.chunk_text(text, limit=400)

    assert len(chunks) > 1
    assert all(len(chunk) <= 400 for chunk in chunks)
    # Nothing lost and nothing invented: every word survives, in order.
    assert " ".join(chunks).split() == text.split()


def test_a_sentence_longer_than_the_limit_still_splits():
    """Falls back to whitespace rather than returning an over-long chunk."""
    text = "palavra " * 200  # one "sentence", no terminator
    chunks = translator.chunk_text(text, limit=200)

    assert all(len(chunk) <= 200 for chunk in chunks)
    assert " ".join(chunks).split() == text.split()


def test_a_single_word_longer_than_the_limit_passes_through_whole():
    """Truncating it would silently change the text, which is worse than a long chunk."""
    monster = "a" * 500
    assert translator.chunk_text(monster, limit=100) == [monster]


# --- the google_free provider -----------------------------------------------
#
# Every test here names the provider explicitly. It used to rely on the default,
# which broke the moment the default moved to mymemory - and a test that only
# passes while a default holds still is testing the default, not the provider.


def google_settings(settings, **over):
    return settings.model_copy(update={"translation_provider": "google_free", **over})


def test_the_request_we_send_is_the_one_the_endpoint_expects(settings):
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["params"] = dict(request.url.params)
        return google_free_response(EN_TRANSLATION)

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        result = translator.translate(PT_DESCRIPTION, "pt", google_settings(settings), http)

    assert result.text == EN_TRANSLATION
    assert result.source_language == "pt"
    assert result.target_language == "en"
    assert result.provider == "google_free"
    assert seen["params"] == {
        "client": "gtx",
        "sl": "pt",
        "tl": "en",
        "dt": "t",
        "q": PT_DESCRIPTION,
    }


def test_several_segments_concatenate_with_no_separator(settings):
    """The endpoint splits a long chunk itself and keeps spaces inside segments."""

    def handler(_: httpx.Request) -> httpx.Response:
        return google_free_response("First part. ", "Second part.")

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        result = translator.translate(PT_DESCRIPTION, "pt", google_settings(settings), http)

    assert result.text == "First part. Second part."


def test_the_stored_language_is_normalised_by_translate_itself(settings):
    """A caller passing `Portuguese` gets the same request as one passing `pt`."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["sl"] == "pt"
        return google_free_response(EN_TRANSLATION)

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        assert (
            translator.translate(
                PT_DESCRIPTION, "Portuguese", google_settings(settings), http
            ).source_language
            == "pt"
        )


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(429, text="rate limited"),
        httpx.Response(200, text="not json at all"),
        httpx.Response(200, json={"unexpected": "shape"}),
        httpx.Response(200, json=[[], None, "pt"]),
    ],
)
def test_an_unusable_answer_raises_rather_than_returning_junk(settings, response):
    """Half a translation is worse than an error: the reader cannot tell."""
    with httpx.Client(transport=httpx.MockTransport(lambda _: response)) as http:
        with pytest.raises(translator.TranslationUnavailable):
            translator.translate(PT_DESCRIPTION, "pt", google_settings(settings), http)


def test_a_transport_failure_does_not_leak_hosts_into_the_message(settings):
    def handler(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("[Errno 8] nodename nor servname provided")

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(translator.TranslationUnavailable) as caught:
            translator.translate(PT_DESCRIPTION, "pt", google_settings(settings), http)

    assert "Errno" not in str(caught.value)
    assert "translation service" in str(caught.value)


def test_an_unknown_provider_is_named_rather_than_silently_skipped(settings):
    broken = settings.model_copy(update={"translation_provider": "nonesuch"})
    with pytest.raises(translator.TranslationUnavailable) as caught:
        translator.translate(PT_DESCRIPTION, "pt", broken)
    assert "nonesuch" in str(caught.value)


# --- the mymemory provider --------------------------------------------------
#
# It is the default because it is the only keyless option that answers from a
# datacenter. Google's endpoint returns 429 to Railway's egress IP whatever
# headers are sent, which was found by calling it from production - not by
# reading anything.


def mymemory_response(text: str, *, status: int | str = 200, detail: str = "") -> httpx.Response:
    """Always HTTP 200. The real status is in the body - that is the whole trap."""
    return httpx.Response(
        200,
        json={
            "responseData": {"translatedText": text},
            "responseStatus": status,
            "responseDetails": detail,
        },
    )


def mymemory_settings(settings, **over):
    return settings.model_copy(update={"translation_provider": "mymemory", **over})


def test_mymemory_sends_the_language_pair_it_expects(settings):
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(dict(request.url.params))
        return mymemory_response("English text")

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        result = translator.translate(PT_DESCRIPTION, "pt", mymemory_settings(settings), http)

    assert result.text == "English text"
    assert result.provider == "mymemory"
    assert seen["langpair"] == "pt|en"
    # No contact address configured, so `de` is omitted rather than sent empty -
    # an empty one is treated as invalid.
    assert "de" not in seen


def test_a_contact_address_is_forwarded_because_it_raises_the_daily_allowance(settings):
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(dict(request.url.params))
        return mymemory_response("English text")

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        translator.translate(
            PT_DESCRIPTION,
            "pt",
            mymemory_settings(settings, translation_contact_email="tenders@sdsmanager.com"),
            http,
        )

    assert seen["de"] == "tenders@sdsmanager.com"


def test_a_failure_reported_inside_an_http_200_is_still_a_failure(settings):
    """The Slack lesson (D22), in a second place: read the body, not the code.

    Trusting the status code here would store the string "QUERY LENGTH LIMIT
    EXCEEDED" as a tender's English description, and the cache would keep it
    for ever.
    """
    refusal = mymemory_response(
        "QUERY LENGTH LIMIT EXCEEDED. MAX ALLOWED QUERY : 500 CHARS",
        status=403,
        detail="QUERY LENGTH LIMIT EXCEEDED. MAX ALLOWED QUERY : 500 CHARS",
    )
    with httpx.Client(transport=httpx.MockTransport(lambda _: refusal)) as http:
        with pytest.raises(translator.TranslationUnavailable):
            translator.translate(PT_DESCRIPTION, "pt", mymemory_settings(settings), http)


def test_running_out_of_free_allowance_says_so_rather_than_blaming_the_network(settings):
    """A daily cap is a different action for the reader than a transient error."""
    exhausted = mymemory_response(
        "",
        status=429,
        detail="YOU USED ALL AVAILABLE FREE TRANSLATIONS FOR TODAY",
    )
    with httpx.Client(transport=httpx.MockTransport(lambda _: exhausted)) as http:
        with pytest.raises(translator.TranslationUnavailable) as caught:
            translator.translate(PT_DESCRIPTION, "pt", mymemory_settings(settings), http)

    assert "daily allowance" in str(caught.value)


def test_the_provider_cap_beats_a_larger_configured_chunk_size(settings):
    """500 is a hard limit, so an operator must not be able to configure past it.

    Without this, TRANSLATION_MAX_CHUNK_CHARS=4000 with the mymemory provider
    produces a 403 inside a 200 on every notice over 500 characters - which
    reads as "the service is broken" from the outside.
    """
    lengths: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        lengths.append(len(request.url.params["q"]))
        return mymemory_response("part. ")

    long_text = " ".join(f"Sentença número {n} com algum texto." for n in range(120))
    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        translator.translate(
            long_text,
            "pt",
            mymemory_settings(settings, translation_max_chunk_chars=4000),
            http,
        )

    assert lengths, "the provider was never called"
    assert max(lengths) <= 500, f"sent a chunk of {max(lengths)} chars at a 500-char provider"


def test_a_smaller_configured_chunk_still_wins(settings):
    """The cap is a ceiling, not an override: a deliberate 200 stays 200."""
    lengths: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        lengths.append(len(request.url.params["q"]))
        return mymemory_response("part. ")

    long_text = " ".join(f"Sentença número {n} com algum texto." for n in range(120))
    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        translator.translate(
            long_text,
            "pt",
            mymemory_settings(settings, translation_max_chunk_chars=200),
            http,
        )

    assert max(lengths) <= 200


def test_the_default_provider_is_the_one_that_works_in_production(settings):
    """Pinned deliberately. google_free answers 429 from Railway's egress IP."""
    from app.settings.config import Settings

    assert Settings(_env_file=None, database_url="sqlite://").translation_provider == "mymemory"
    assert set(translator._PROVIDERS) == {"google_free", "mymemory"}


# --- the endpoint -----------------------------------------------------------


@pytest.fixture
def one_call_only(monkeypatch):
    """Stub `translate` and count how often it is reached.

    The count is the point: a cache that returns the right text while calling
    the provider every time looks identical from the response body alone.
    """
    calls: list[tuple[str, str | None]] = []

    def fake(text, source_language, settings=None, client=None):
        calls.append((text, source_language))
        return translator.Translated(
            text=EN_TRANSLATION,
            source_language="pt",
            target_language="en",
            provider="google_free",
        )

    monkeypatch.setattr(translator, "translate", fake)
    return calls


def test_translating_a_notice_returns_english_and_stores_it(client, db_session, one_call_only):
    row = add_tender(db_session, "pt-1")

    response = client.post(f"/api/tenders/{row.id}/translate")

    assert response.status_code == 200
    body = response.json()
    assert body["text"] == EN_TRANSLATION
    assert body["source_language"] == "pt"
    assert body["target_language"] == "en"
    assert body["cached"] is False
    assert len(one_call_only) == 1

    # Asserted against the database, not the response: a 200 with no row behind
    # it would make the second press call the provider again.
    stored = db_session.query(TenderTranslation).filter_by(tender_id=row.id).one()
    assert stored.text == EN_TRANSLATION
    assert stored.provider == "google_free"


def test_the_second_press_is_served_from_the_cache(client, db_session, one_call_only):
    row = add_tender(db_session, "pt-1")

    first = client.post(f"/api/tenders/{row.id}/translate").json()
    second = client.post(f"/api/tenders/{row.id}/translate").json()

    assert first["text"] == second["text"]
    assert first["cached"] is False
    assert second["cached"] is True
    # The whole cost control for this feature. Not a cooldown - this.
    assert len(one_call_only) == 1
    assert db_session.query(TenderTranslation).count() == 1


def test_an_english_notice_is_refused_rather_than_translated(client, db_session, one_call_only):
    row = add_tender(db_session, "en-1", language="English", description="Cloud storage framework.")

    response = client.post(f"/api/tenders/{row.id}/translate")

    assert response.status_code == 409
    assert len(one_call_only) == 0
    assert db_session.query(TenderTranslation).count() == 0


def test_a_notice_with_no_description_is_refused(client, db_session, one_call_only):
    row = add_tender(db_session, "pt-empty", description=None)

    assert client.post(f"/api/tenders/{row.id}/translate").status_code == 409
    assert len(one_call_only) == 0


def test_a_notice_with_no_recorded_language_is_refused(client, db_session, one_call_only):
    row = add_tender(db_session, "pt-nolang", language=None)

    assert client.post(f"/api/tenders/{row.id}/translate").status_code == 409
    assert len(one_call_only) == 0


def test_a_missing_notice_is_a_404(client):
    assert client.post("/api/tenders/999999/translate").status_code == 404


def test_an_upstream_failure_is_a_502_carrying_a_readable_sentence(client, db_session, monkeypatch):
    row = add_tender(db_session, "pt-1")

    def boom(*_args, **_kwargs):
        raise translator.TranslationUnavailable("Could not reach the translation service.")

    monkeypatch.setattr(translator, "translate", boom)

    response = client.post(f"/api/tenders/{row.id}/translate")

    assert response.status_code == 502
    assert response.json()["detail"] == "Could not reach the translation service."
    # Nothing cached on failure, or the button would be permanently broken for
    # that notice with no way to retry.
    assert db_session.query(TenderTranslation).count() == 0


def test_translating_needs_a_session(db_session, monkeypatch, settings):
    """Every route is private (D26) and a new one must not be the exception."""
    from fastapi.testclient import TestClient

    from tests.conftest import _build_app

    app = _build_app(db_session, monkeypatch, settings)
    with TestClient(app) as anonymous:
        assert anonymous.post("/api/tenders/1/translate").status_code == 401
