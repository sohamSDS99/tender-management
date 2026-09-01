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

import json
from pathlib import Path

import httpx
import pytest

from app.models import Tender, TenderTranslation
from app.services import language, translator
from tests.test_ingest import NOW

PT_DESCRIPTION = (
    "O objeto da presente dispensa de licitação é a escolha da proposta mais "
    "vantajosa para a contratação de solução corporativa de armazenamento."
)
EN_TRANSLATION = (
    "The purpose of this bidding exemption is the choice of the most advantageous "
    "proposal for contracting a corporate storage solution."
)
#: A description that is unambiguously English, at the length real ones run to.
#: `EN_TRANSLATION` would do, but a fixture that doubles as the expected output
#: of a translation makes it impossible to tell which property a test is pinning.
EN_DESCRIPTION = (
    "The scope of the contract to be awarded consists of the design, engineering, "
    "procurement, installation, certification and commissioning of an integrated "
    "heat generation system, including all associated works."
)
#: One real German TED description, verbatim, stored - as every TED notice is -
#: with `language='eng'`. Kept here so the regression has a name a reader
#: recognises: this notice had no Translate button until D33 was amended.
DE_DESCRIPTION = (
    "Die ausgeschriebenen Arbeiten umfassen sämtliche Metallbauarbeiten im äußeren "
    "Bereich im Bestand und für den Neubau einschließlich erforderlicher "
    "Rückbauarbeiten im Zusammenhang mit geänderten Öffnungen."
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
        # A stored "English" that is contradicted by the text loses. This is the
        # TED case and the whole reason D33 was amended: TED reads its language
        # off the notice *title*, which it translates into all 24 EU languages,
        # so `eng` is stored on 100% of its notices - including the German ones.
        ("en", PT_DESCRIPTION, True),
        ("eng", PT_DESCRIPTION, True),
        ("English", PT_DESCRIPTION, True),
        # Nothing to translate, whatever the language says.
        ("pt", None, False),
        ("pt", "   ", False),
        # An unrecorded language is no longer left alone. "Left alone" is
        # indistinguishable, to a reader, from a button that does not work.
        (None, PT_DESCRIPTION, True),
        ("Klingon", PT_DESCRIPTION, True),
        # Genuinely English text keeps its silence, however the feed spells it.
        ("eng", EN_DESCRIPTION, False),
        (None, EN_DESCRIPTION, False),
        # Two real TED descriptions, in the two words the buyer actually wrote.
        # Short is not the same as unknowable: both classify as German at 1.00.
        ("eng", "Küchentechnik Wartung", True),
        ("eng", "Innenputz-/ Malerarbeiten", True),
        # ...but three English content words carry almost no signal (`Cloud
        # storage framework.` reads as Dutch at 0.33), so an unconfident reading
        # does not get to overturn a feed that positively claimed English.
        ("eng", "Cloud storage framework.", False),
        # With no claim to overturn, the same weak reading is enough - there is
        # nothing on the other side of the scale.
        (None, "Cloud storage framework.", True),
        # Real TED descriptions. There is no text here to send anywhere.
        ("eng", "-", False),
        ("eng", "...", False),
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
    english = add_tender(db_session, "en-1", language="eng", description=EN_DESCRIPTION)
    # Stored exactly as TED stores every one of its notices, German ones included.
    german = add_tender(db_session, "ted-de", language="eng", description=DE_DESCRIPTION)

    assert client.get(f"/api/tenders/{foreign.id}").json()["needs_translation"] is True
    assert client.get(f"/api/tenders/{english.id}").json()["needs_translation"] is False
    assert client.get(f"/api/tenders/{german.id}").json()["needs_translation"] is True


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
    """Pinned deliberately, and the pin has moved once for a measured reason.

    It was `mymemory`, because `google_free` answers 429 from Railway's egress
    IP. It is now `deepl`, because `mymemory` rations 5,000 characters a day per
    IP - about six notices - and production spent it, so a reader who pressed
    Translate once was told the free service had used its daily allowance. A
    keyless service can only ration by address; a key is what removes the
    ceiling rather than raising it. D35.
    """
    from app.settings.config import Settings

    assert Settings(_env_file=None, database_url="sqlite://").translation_provider == "deepl"
    assert set(translator._PROVIDERS) == {"google_free", "mymemory", "deepl"}


# --- the deepl provider -----------------------------------------------------


def deepl_settings(settings, **over):
    return settings.model_copy(update={"translation_provider": "deepl", "deepl_api_key": "test-key", **over})


def deepl_response(*pairs: tuple[str, str]) -> httpx.Response:
    """DeepL's real shape: one object per input text, in order, each self-describing."""
    return httpx.Response(
        200,
        json={"translations": [{"text": text, "detected_source_language": lang} for text, lang in pairs]},
    )


def test_deepl_sends_the_whole_notice_as_one_request(settings):
    """`text` is an array, so a long description costs one request, not one per chunk.

    The keyless providers loop and spend a request per chunk; this one does not,
    and that is most of why a long notice is cheap here.
    """
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content)
        seen["calls"] = int(seen.get("calls", 0)) + 1
        return deepl_response(("One.", "DE"), ("Two.", "DE"))

    client = httpx.Client(transport=httpx.MockTransport(handler))
    long_text = " ".join(f"Satz nummer {n} mit etwas text." for n in range(300))
    result = translator.translate(long_text, "deu", deepl_settings(settings), client)

    assert seen["calls"] == 1
    assert seen["url"] == "https://api.deepl.com/v2/translate"
    assert seen["auth"] == "DeepL-Auth-Key test-key"
    assert seen["body"]["target_lang"] == "EN-GB"
    assert len(seen["body"]["text"]) > 1
    assert result.text == "One. Two."


def test_deepl_never_sends_a_source_language(settings):
    """Its own detection is better than ours, so overriding it would be a downgrade.

    `Küchentechnik Wartung` is German; DeepL says so and `language.detect` calls
    it Swedish at 0.9966. Sending our guess would replace the better answer with
    the worse one, which is precisely D33's "confident translation of the wrong
    thing".
    """
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return deepl_response(("Kitchen technology maintenance", "DE"))

    client = httpx.Client(transport=httpx.MockTransport(handler))
    # A stored `pt` would be handed to any other provider as the source.
    result = translator.translate("Küchentechnik Wartung", "pt", deepl_settings(settings), client)

    assert "source_lang" not in seen["body"]
    # ...and what gets reported is what DeepL actually translated from, not `pt`.
    assert result.source_language == "de"


def test_a_free_tier_key_goes_to_the_free_host(settings):
    """A `:fx` suffix is the only thing that distinguishes the two tiers.

    Sending a Pro key to the free host answers **403 "Wrong endpoint"**, which
    reads as an authentication failure and is not one - so the host is derived
    from the key rather than configured separately and got wrong.
    """
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return deepl_response(("English.", "DE"))

    client = httpx.Client(transport=httpx.MockTransport(handler))
    translator.translate(
        "Ein deutscher satz.", "deu", deepl_settings(settings, deepl_api_key="abc:fx"), client
    )

    assert seen["url"] == "https://api-free.deepl.com/v2/translate"
    assert translator._deepl_host("abc:fx") == "api-free.deepl.com"
    assert translator._deepl_host("abc") == "api.deepl.com"


def test_a_missing_key_says_which_variable_to_set(settings):
    """The default provider is keyed now, so an unconfigured deployment must say so.

    "Could not reach the translation service" would send somebody to look at the
    network for a problem that is one variable.
    """
    with pytest.raises(translator.TranslationUnavailable) as exc:
        translator.translate("Ein deutscher satz.", "deu", deepl_settings(settings, deepl_api_key=""))

    assert "DEEPL_API_KEY" in str(exc.value)


@pytest.mark.parametrize(
    "status,expected",
    [
        (403, "key"),
        (456, "character allowance"),
        (429, "busy"),
        (413, "too long"),
        (500, "Try again in a minute"),
    ],
)
def test_deepl_reports_each_failure_in_words_a_reader_can_act_on(settings, status, expected):
    """DeepL puts the failure in the status code, unlike the two providers either side.

    Worth a test rather than a comment: this file's other two providers both hide
    failure inside an HTTP 200, so the reflex when reading them is not to trust a
    status code.
    """
    client = httpx.Client(transport=httpx.MockTransport(lambda _r: httpx.Response(status)))

    with pytest.raises(translator.TranslationUnavailable) as exc:
        translator.translate("Ein deutscher satz.", "deu", deepl_settings(settings), client)

    assert expected in str(exc.value)


def test_the_deepl_key_is_redacted_from_anything_logged(settings):
    """A key in a log line is a key in the logs, the same lesson SAM.gov taught."""
    from app.settings.config import redact

    configured = deepl_settings(settings, deepl_api_key="super-secret-key")

    assert "super-secret-key" not in redact("failed with super-secret-key", configured)


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
    """Judged on the text now, not on what the feed called it.

    Both halves are the point: the description really is English, so no provider
    call is made and nothing is cached - a round trip through a translator would
    hand back approximately the input, and the cache would keep it for ever.
    """
    row = add_tender(db_session, "en-1", language="English", description=EN_DESCRIPTION)

    response = client.post(f"/api/tenders/{row.id}/translate")

    assert response.status_code == 409
    assert len(one_call_only) == 0
    assert db_session.query(TenderTranslation).count() == 0


def test_a_notice_with_no_description_is_refused(client, db_session, one_call_only):
    row = add_tender(db_session, "pt-empty", description=None)

    assert client.post(f"/api/tenders/{row.id}/translate").status_code == 409
    assert len(one_call_only) == 0


def test_a_notice_with_no_recorded_language_is_translated_from_its_text(client, db_session, one_call_only):
    """The reversal of D33's "left alone, not guessed at", stated as a test.

    Some feeds leave `language` empty; under the old rule those notices were
    unreadable for ever with no way for a reader to tell why the button was
    missing. The text is Portuguese and says so plainly, so the button works and
    the response reports the language it was actually translated from.
    """
    row = add_tender(db_session, "pt-nolang", language=None)

    response = client.post(f"/api/tenders/{row.id}/translate")

    assert response.status_code == 200
    assert response.json()["source_language"] == "pt"
    assert len(one_call_only) == 1


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


# ---------------------------------------------------------------------------
# The gold set
# ---------------------------------------------------------------------------
#
# 33 real notices, fetched live from all seven reachable sources on 2026-09-01
# and stored verbatim - the stored `language` is what the connector actually
# wrote, and the description is what the feed actually published.
#
# It exists because this bug was invisible to a suite built from invented
# fixtures. Every hand-written test case had a `language` that was either right
# or absent, so the rule "trust the column" passed all of them; TED's column is
# neither, and no fixture in the repository had ever been shaped like that. A
# sample of production is the only thing that would have caught it, so a sample
# of production is now committed.

GOLD_SET = json.loads(
    (Path(__file__).parent / "fixtures" / "language_gold_set.json").read_text(encoding="utf-8")
)


def _gold_id(case: dict) -> str:
    return f"{case['source']}-{case['detected']}-{case['description'][:24]}"


@pytest.mark.parametrize("case", GOLD_SET, ids=_gold_id)
def test_the_button_decision_on_real_notices(case):
    """Every notice in the gold set gets the button decision a reader would make.

    Read the failures rather than regenerating the file. A change here means the
    detector's answer moved on text that has not moved, which is either a better
    model or a worse one - and the fixture cannot tell you which.
    """
    assert (
        translator.needs_translation(case["stored_language"], case["description"])
        is case["needs_translation"]
    )


def test_every_foreign_notice_in_the_gold_set_is_offered_a_button():
    """The requirement, stated once as a whole rather than case by case.

    "Not written in English" is the test, and it is not the same as "does not
    classify as English". A bilingual CanadaBuys notice - the English, a blank
    line, then the same text in French - classifies as **French at 1.00**,
    because French carries more signal per character than English does. It is
    still written in English, and the reader can already read it.

    So the invariant excludes notices that *contain* English rather than being
    loosened. Phrased over the corpus instead of per-notice so that adding a
    foreign notice to the fixture cannot quietly pass by being forgotten.
    """
    missed = [
        case
        for case in GOLD_SET
        if case["detected"] not in (None, "en")
        and not language.contains_english(case["description"])
        and not case["needs_translation"]
    ]
    assert missed == []


def test_a_bilingual_notice_is_left_alone_because_the_reader_has_english():
    """The regression this rule exists for, on the notices that caused it.

    127 of 256 stored CanadaBuys notices grew a Translate button they did not
    need, and it took production data to see it - the live sample this fixture
    was first built from was a seven-day window that happened to hold almost no
    bilingual notices. Each of these reads as French when classified whole and
    opens with English the reader can already read.
    """
    bilingual = [case for case in GOLD_SET if "bilingual" in (case.get("note") or "")]

    assert len(bilingual) >= 3, "the gold set must keep real bilingual notices"
    for case in bilingual:
        assert case["detected"] == "fr", "classified foreign when read whole..."
        assert language.contains_english(case["description"]), "...but contains English"
        assert case["needs_translation"] is False


def test_a_short_english_header_does_not_excuse_a_foreign_notice():
    """The share is weighted by characters, so a header cannot outvote the body.

    Counting segments instead would let `NOTICE OF PROPOSED PROCUREMENT` carry
    the same weight as three thousand characters of French.
    """
    french = (
        "Le présent marché a pour objet l’exécution du nettoyage des locaux de l’EFS "
        "Bretagne sur le site de Quimper. Les spécifications techniques sont détaillées "
        "dans le cahier des clauses techniques particulières. Les quantités estimatives "
        "sont indiquées au bordereau des prix unitaires du marché public."
    )
    with_header = f"NOTICE OF PROPOSED PROCUREMENT\n\n{french}"

    assert language.contains_english(with_header) is False
    assert translator.needs_translation("en", with_header) is True


def test_the_gold_set_still_contains_the_notices_that_proved_the_bug():
    """A fixture trimmed down to green is not a fixture.

    TED is the whole of European procurement and stores `language='eng'` on every
    notice it publishes; if its foreign notices ever leave this file, the
    regression stops being covered and nothing else here would notice.
    """
    ted_foreign = [c for c in GOLD_SET if c["source"] == "ted" and c["detected"] not in (None, "en")]
    assert len(ted_foreign) >= 11
    assert {c["detected"] for c in ted_foreign} >= {"de", "fr"}
    assert all(c["stored_language"] == "eng" for c in ted_foreign)
    assert all(c["needs_translation"] for c in ted_foreign)


def test_an_all_caps_english_notice_is_not_foreign():
    """The lowercasing guard, pinned to the two notices that needed it.

    py3langid is trained on natural-case text, so ALL-CAPS English reads as
    Maltese (0.91) and Xhosa (0.64) - and capitalised headers are the house style
    of procurement writing, so this is the common case here, not a curiosity.
    Remove the `.lower()` in `language.detect` and these turn red.
    """
    shouty = [c for c in GOLD_SET if c["description"].strip()[:40].isupper()]
    assert len(shouty) >= 3, "the gold set must keep some ALL-CAPS English notices"
    for case in shouty:
        assert language.detect(case["description"]).code == "en"
        assert case["needs_translation"] is False
