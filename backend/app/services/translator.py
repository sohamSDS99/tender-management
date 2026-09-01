"""Translate a foreign-language notice into English, on demand.

Two thirds of the corpus is already English; the rest is mostly Brazilian
Portuguese from PNCP, plus a handful of three-letter TED codes and a few
full-word names from the World Bank feed. So this module has two jobs, and they
are deliberately separate:

1. **Decide whether a notice needs translating at all** (`needs_translation`).
   That answer belongs on the server, not in the browser, because the `language`
   column is genuinely inconsistent - `en`, `eng`, `English`, `pt` and `French`
   are all real stored values - and a second normaliser in TypeScript would
   drift from this one. The API sends a boolean; the dashboard renders a button.

   **That column is not merely inconsistent, it is sometimes confidently wrong,
   and the original D33 rule trusted it.** TED stores `language='eng'` on 100%
   of its notices because it reads the language off the notice *title*, which TED
   machine-translates into all 24 EU languages; the description stays in the
   buyer's own. Measured live: 11 of TED's 15 descriptions were German, French or
   Dutch and not one was offered a button. So the stored value is now only
   trusted when it names a *foreign* language - a claim nothing in the corpus was
   found to fake - and a stored "English", or no value at all, is checked against
   the description itself by `app/services/language.py`.

2. **Perform the translation** (`translate`), behind one function so the
   provider is configuration rather than code - the same shape
   `app/services/mailer.py` uses for SMTP.

**On the providers, and why the keyed one is now the default.** A keyless
service can only ration by the caller's address, so both of the keyless options
do - and that ration is what a reader actually hit. The differences are
measured, not theoretical:

- ``deepl`` is the **default** (D35). Keyed, so there is no per-IP ration; a Pro
  key reports a character limit of 10^12. It takes the whole notice as an array
  in one request, detects the source language per element - better than this app
  does - and reports failure in the **status code**, like an ordinary HTTP API.
- ``mymemory`` is the keyless fallback and the only keyless one that answers
  from a datacenter. It caps a request at 500 characters, reports failure as a
  body field inside an HTTP 200, and allows 5,000 characters a day per IP -
  about six notices. Production spent that allowance and the button started
  answering "the free translation service has used its daily allowance", which
  is what D35 exists to fix.
- ``google_free`` gives better English on concatenated legal prose and takes
  8,000 characters at a time, but answers **429 to Railway's egress IP** whatever
  headers are sent - so it is the right choice from a laptop or the LAN
  deployment, and useless in production.

Every translation is cached in `tender_translations`, so a notice is fetched once
and never again - that is what keeps the request count proportional to *new*
foreign notices a human actually opens, rather than to page views. That mattered
most when the provider was rationed; it still matters, because it is also what
keeps a keyed provider's bill proportional to what somebody actually read.

If it ever stops working, the fix is a new ``_PROVIDERS`` entry and a changed
``TRANSLATION_PROVIDER``, not a rewrite: `translate` is the only function that
knows a provider exists, and `TranslationUnavailable` is the only thing callers
handle.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

import httpx

from app.logging_config import log_ctx
from app.services import language as language_detect
from app.settings.config import Settings, get_settings

logger = logging.getLogger(__name__)

#: The language we translate *into*. Not configurable: the dashboard, the
#: relevance keywords and the people reading both are English, and a second
#: target would need a second cache key everywhere it appears.
TARGET_LANGUAGE = "en"

#: Longest text sent in one request. The endpoint accepted 8,000 characters in
#: testing and the longest stored description is 5,837, so in practice every
#: notice goes in a single call - this is the safety net for the day one does
#: not, not the common path. Splitting happens on sentence boundaries so a
#: chunk is never cut mid-word.
MAX_CHUNK_CHARS = 4000

#: Every spelling of English this corpus actually stores, plus the ISO codes the
#: feeds could start sending. Compared lowercased, so `English` and `ENG` both
#: land here.
_ENGLISH = frozenset({"en", "eng", "english", "en-gb", "en-us", "en_gb", "en_us"})

#: Three-letter and full-word forms mapped to the two-letter code the translate
#: endpoint expects. Only languages these eight sources can emit are listed; an
#: unknown value is reported as unknown rather than guessed at, because guessing
#: a source language is how you get a confident translation of the wrong thing.
_TO_ISO_639_1 = {
    "por": "pt",
    "portuguese": "pt",
    "pt-br": "pt",
    "pt_br": "pt",
    "fra": "fr",
    "fre": "fr",
    "french": "fr",
    "spa": "es",
    "spanish": "es",
    "deu": "de",
    "ger": "de",
    "german": "de",
    "ita": "it",
    "italian": "it",
    "nld": "nl",
    "dut": "nl",
    "dutch": "nl",
    "swe": "sv",
    "swedish": "sv",
    "dan": "da",
    "danish": "da",
    "fin": "fi",
    "finnish": "fi",
    "pol": "pl",
    "polish": "pl",
    "ron": "ro",
    "rum": "ro",
    "romanian": "ro",
    "ell": "el",
    "gre": "el",
    "greek": "el",
    "ces": "cs",
    "cze": "cs",
    "czech": "cs",
    "hun": "hu",
    "hungarian": "hu",
    "bul": "bg",
    "bulgarian": "bg",
    "hrv": "hr",
    "croatian": "hr",
    "slk": "sk",
    "slo": "sk",
    "slovak": "sk",
    "slv": "sl",
    "slovenian": "sl",
    "est": "et",
    "estonian": "et",
    "lav": "lv",
    "latvian": "lv",
    "lit": "lt",
    "lithuanian": "lt",
    "gle": "ga",
    "irish": "ga",
    "mlt": "mt",
    "maltese": "mt",
    "nor": "no",
    "norwegian": "no",
    "ara": "ar",
    "arabic": "ar",
    "rus": "ru",
    "russian": "ru",
    "tur": "tr",
    "turkish": "tr",
    "ukr": "uk",
    "ukrainian": "uk",
    "zho": "zh",
    "chi": "zh",
    "chinese": "zh",
    "jpn": "ja",
    "japanese": "ja",
    "kor": "ko",
    "korean": "ko",
}


class TranslationUnavailable(RuntimeError):
    """The provider could not be reached, or answered something unusable.

    Carries a sentence written for whoever pressed the button, because that is
    where it is displayed. The cause is logged, never shown - an upstream error
    body is not a message for a reader.
    """


@dataclass(frozen=True)
class Translated:
    """One completed translation, and where it came from.

    ``source_language`` may be the empty string, which means "nobody could name
    it" - neither the feed, nor the classifier with any confidence, nor the
    provider. The dashboard already renders that as "another language"
    (`languageLabel` in `frontend/src/labels.ts`), which is the honest caption.
    Filling it with a low-confidence guess instead would put a language name a
    reader cannot check underneath a translation they cannot check.
    """

    text: str
    source_language: str
    target_language: str
    provider: str


@dataclass(frozen=True)
class ProviderResult:
    """One provider's answer: the translated chunks, and what it saw the source as.

    ``detected_source`` is only populated when the provider was asked to detect
    the language itself and told us what it found. It is the best available
    answer in that case - the provider translated from it, so it is what actually
    happened, rather than what this app guessed would happen.
    """

    texts: list[str]
    detected_source: str | None = None


#: Handed to a provider in place of a source code when neither the feed nor the
#: classifier could name the language confidently. Each provider spells its own
#: autodetect differently, so the sentinel is internal and never leaves this
#: module - `_mymemory` in particular refuses the obvious `auto` with a 200.
AUTO_DETECT = None


def normalise_language(raw: str | None) -> str | None:
    """A two-letter code, or None when the stored value means nothing to us.

    None is a real answer, not a failure: `language` is nullable and some feeds
    leave it empty. Callers must treat unknown as "do not offer to translate",
    which is why this never falls back to a default.
    """
    if not raw:
        return None
    cleaned = raw.strip().lower().replace(" ", "")
    if not cleaned:
        return None
    if cleaned in _ENGLISH:
        return "en"
    if cleaned in _TO_ISO_639_1:
        return _TO_ISO_639_1[cleaned]
    # A bare two-letter code we have no name for is still usable: the endpoint
    # takes ISO 639-1 and will reject a code it does not know, which is a better
    # outcome than refusing to try.
    if len(cleaned) == 2 and cleaned.isalpha():
        return cleaned
    return None


def is_english(raw: str | None) -> bool:
    """Whether a stored language value means English. Unknown is not English."""
    return normalise_language(raw) == "en"


def needs_translation(language: str | None, description: str | None) -> bool:
    """Whether the dashboard should offer a Translate button for this notice.

    Three cases, in this order, and the order is the design:

    1. **No description** - nothing to translate, no button. Unchanged.
    2. **The feed named a foreign language** - believe it, without reading the
       text. PNCP says `pt` on 112 of 112 notices and is right every time; the
       stored code is also better than a classifier at telling Portuguese from
       Spanish on two lines of legal boilerplate. Nothing in the corpus was found
       claiming a foreign language it was not in, so there is no measured reason
       to second-guess this half.
    3. **The feed said English, or said nothing** - the description decides.
       This is the case D33 got wrong. It assumed a stored language was either
       right or absent, and TED's is neither: it is `eng` on every notice
       including the German ones. An unrecorded language is no longer left alone
       either, because "left alone" is indistinguishable, to a reader, from a
       feature that does not work.

    Case 3 splits once more, on **how much evidence it takes to contradict the
    feed**, and this is the only subtle thing in the function:

    - The feed **claimed English**. Overturning a positive claim needs a
      confident classification. A three-word English description carries almost
      no signal - `Cloud storage framework.` classifies as Dutch at 0.33 - and
      without this the button would appear on every short English notice.
    - The feed **claimed nothing**. There is no claim to overturn, so any
      non-English reading is enough.

    That asymmetry costs nothing measurable: across 413 live notices every
    genuinely foreign description scored **1.00**, including the two-word German
    ones (`Küchentechnik Wartung`, `Innenputz-/ Malerarbeiten`), while every
    unconfident reading came from English text or from a description with no
    words in it. Recall on real foreign notices stays at 100% and the short-text
    false positive goes away.

    Too short to judge is the remaining exception, and it is not a hedge - `-`
    and `...` are real TED descriptions with nothing to send to a translator.
    """
    body = (description or "").strip()
    if not body:
        return False
    code = normalise_language(language)
    if code is not None and code != TARGET_LANGUAGE:
        return True
    detection = language_detect.detect(body)
    if detection is None or detection.is_english:
        return False
    if code == TARGET_LANGUAGE:
        return detection.is_confident
    return True


def chunk_text(text: str, limit: int = MAX_CHUNK_CHARS) -> list[str]:
    """Split on sentence boundaries, never mid-word, each piece under ``limit``.

    A single sentence longer than the limit is split on whitespace as a last
    resort; a single *word* longer than the limit is passed through whole,
    because truncating it would silently change the text.
    """
    text = text.strip()
    if len(text) <= limit:
        return [text] if text else []

    pieces: list[str] = []
    current = ""
    for sentence in _sentences(text):
        if len(sentence) > limit:
            if current:
                pieces.append(current)
                current = ""
            pieces.extend(_split_on_space(sentence, limit))
            continue
        candidate = f"{current} {sentence}".strip() if current else sentence
        if len(candidate) <= limit:
            current = candidate
        else:
            pieces.append(current)
            current = sentence
    if current:
        pieces.append(current)
    return pieces


def _sentences(text: str) -> list[str]:
    """Crude but adequate: break after . ! ? and keep the terminator."""
    out: list[str] = []
    buffer = ""
    for char in text:
        buffer += char
        if char in ".!?":
            out.append(buffer.strip())
            buffer = ""
    if buffer.strip():
        out.append(buffer.strip())
    return out


def _split_on_space(sentence: str, limit: int) -> list[str]:
    out: list[str] = []
    current = ""
    for word in sentence.split(" "):
        candidate = f"{current} {word}".strip() if current else word
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            out.append(current)
        # A word longer than the whole limit goes through intact on purpose.
        current = word
    if current:
        out.append(current)
    return out


def _google_free(
    chunks: list[str], source: str | None, settings: Settings, client: httpx.Client
) -> ProviderResult:
    """Google's keyless endpoint. Returns one translated string per chunk.

    The response is a nested array, not an object: ``[[[translated, original,
    ...], ...], null, detected_source, ...]``. Several segments come back for a
    long chunk and they concatenate with no separator - the endpoint splits on
    sentence boundaries and keeps the trailing spaces inside each segment.

    ``sl=auto`` is this endpoint's autodetect, and index 2 of the response is the
    language it settled on - which is why the nested array is indexed rather than
    unpacked.
    """
    out: list[str] = []
    detected: str | None = None
    for chunk in chunks:
        response = client.get(
            "https://translate.googleapis.com/translate_a/single",
            params={
                "client": "gtx",
                "sl": source or "auto",
                "tl": TARGET_LANGUAGE,
                "dt": "t",
                "q": chunk,
            },
            timeout=settings.translation_timeout_seconds,
        )
        if response.status_code != 200:
            raise TranslationUnavailable(
                "The translation service refused the request. Try again in a minute."
            )
        try:
            payload = response.json()
            segments = payload[0]
            translated = "".join(part[0] for part in segments if part and part[0])
            if detected is None and len(payload) > 2 and isinstance(payload[2], str):
                detected = payload[2]
        except (json.JSONDecodeError, IndexError, KeyError, TypeError) as exc:
            raise TranslationUnavailable(
                "The translation service answered in a shape this app does not understand."
            ) from exc
        if not translated.strip():
            raise TranslationUnavailable("The translation service returned nothing.")
        out.append(translated)
    return ProviderResult(texts=out, detected_source=detected)


#: MyMemory's autodetect, spelled exactly this way. `auto` - the obvious guess,
#: and what Google's endpoint uses - is refused with
#: ``'AUTO' IS AN INVALID SOURCE LANGUAGE`` inside an HTTP **200**, so getting
#: this wrong would not raise anywhere; it would store that sentence as a
#: notice's English description and cache it for ever. Verified against the live
#: endpoint, which answered `Küchentechnik Wartung` -> `Kitchen technology
#: maintenance` with `detectedLanguage: "de"`.
_MYMEMORY_AUTODETECT = "Autodetect"


def _mymemory(
    chunks: list[str], source: str | None, settings: Settings, client: httpx.Client
) -> ProviderResult:
    """MyMemory's keyless endpoint. Works from a datacenter IP, unlike Google's.

    **It answers HTTP 200 when it fails**, exactly like Slack's Web API (D22), so
    the body's ``responseStatus`` is the answer and the status code is not. The
    503-shaped case that matters is ``403 QUERY LENGTH LIMIT EXCEEDED``, which is
    why ``MAX_CHUNK_CHARS`` per provider exists rather than one global setting -
    500 characters is a hard cap here and a needless 8x more requests on Google.

    ``de`` is an ordinary contact address, not a credential: supplying one raises
    the anonymous daily allowance from 5,000 characters to 50,000. Omitted when
    unset rather than sent empty, because an empty ``de`` is treated as invalid.
    """
    out: list[str] = []
    detected: str | None = None
    for chunk in chunks:
        pair = f"{source or _MYMEMORY_AUTODETECT}|{TARGET_LANGUAGE}"
        params: dict[str, str] = {"q": chunk, "langpair": pair}
        contact = (settings.translation_contact_email or "").strip()
        if contact:
            params["de"] = contact
        response = client.get(
            "https://api.mymemory.translated.net/get",
            params=params,
            timeout=settings.translation_timeout_seconds,
        )
        try:
            payload = response.json()
        except json.JSONDecodeError as exc:
            raise TranslationUnavailable(
                "The translation service answered in a shape this app does not understand."
            ) from exc

        # The status that matters is in the body. `responseStatus` arrives as an
        # int or a string depending on the failure, so it is compared as a string.
        status = str(payload.get("responseStatus", ""))
        if status != "200":
            detail = str(payload.get("responseDetails") or "")
            log_ctx(
                logger,
                logging.WARNING,
                "translation refused",
                provider="mymemory",
                status=status,
                detail=detail[:200],
            )
            # Two different exhaustion messages, both real and neither containing
            # the same word: "QUERY LENGTH LIMIT EXCEEDED..." for an over-long
            # request and "MYMEMORY WARNING: YOU USED ALL AVAILABLE FREE
            # TRANSLATIONS FOR TODAY..." for the daily allowance. Matching only
            # "LIMIT" told a reader who had run out of quota to try again in a
            # minute, which would never work.
            loud = detail.upper()
            if "USED ALL AVAILABLE" in loud or "AVAILABLE FREE TRANSLATIONS" in loud:
                raise TranslationUnavailable(
                    "The free translation service has used its daily allowance. "
                    "Try again tomorrow, or configure a provider with a key."
                )
            if "LIMIT" in loud:
                raise TranslationUnavailable(
                    "The translation service refused this text as too long. "
                    "This is a configuration problem, not a temporary one."
                )
            raise TranslationUnavailable(
                "The translation service refused the request. Try again in a minute."
            )

        data = payload.get("responseData") or {}
        translated = str(data.get("translatedText") or "")
        if not translated.strip():
            raise TranslationUnavailable("The translation service returned nothing.")
        if detected is None:
            # Only present on an autodetected request, and it is what the
            # provider actually translated from - better than this app's guess.
            found = str(data.get("detectedLanguage") or "").strip()
            if found:
                detected = found
        out.append(translated)
    return ProviderResult(texts=out, detected_source=detected)


#: DeepL's target. `EN` alone is deprecated as a target and DeepL asks for a
#: variant; the corpus is UK and EU procurement, so British English is the one
#: that reads naturally to the people using this dashboard.
_DEEPL_TARGET = "EN-GB"

#: A `:fx` suffix marks a free-tier key, and the two tiers are on different
#: hosts. Sending a Pro key to the free host answers **403 "Wrong endpoint"** -
#: which reads as an authentication failure and is not one, so the host is
#: derived from the key rather than configured separately and got wrong.
_DEEPL_FREE_SUFFIX = ":fx"


def _deepl_host(api_key: str) -> str:
    return "api-free.deepl.com" if api_key.endswith(_DEEPL_FREE_SUFFIX) else "api.deepl.com"


#: DeepL's documented failures, mapped to sentences written for whoever pressed
#: the button. Unlike MyMemory and Slack, DeepL reports failure in the **status
#: code**, which is the ordinary thing to do and worth saying out loud because
#: the two providers either side of it in this file do not.
_DEEPL_ERRORS = {
    403: "The translation service rejected this deployment's key. Check DEEPL_API_KEY.",
    413: "This notice is too long for the translation service to accept in one request.",
    429: "The translation service is busy. Try again in a moment.",
    456: "The translation account has used its character allowance for this billing period.",
}


def _deepl(chunks: list[str], source: str | None, settings: Settings, client: httpx.Client) -> ProviderResult:
    """DeepL, with a key. One request per notice, whatever its length.

    Three things make this different from the keyless pair above, and all three
    are why it is the default now:

    1. **No ration.** The keyless providers ration by IP because that is all a
       keyless service can ration by; a Pro key reports a character limit of
       10^12, which is not a ceiling anybody in this product will meet.
    2. **The whole notice goes in one HTTP request.** `text` is an *array*, and
       DeepL returns one translation per element in order - so a long
       description still chunks on sentence boundaries, but the chunks travel
       together instead of costing a request each.
    3. **``source`` is deliberately ignored.** DeepL detects per element and is
       better at it than this app: it named `Küchentechnik Wartung` as German,
       which `language.detect` called Swedish at 0.9966. Passing our own guess
       would override a better answer with a worse one, and D33's "confident
       translation of the wrong thing" is exactly what that produces. The
       detected language is reported back instead.
    """
    api_key = (settings.deepl_api_key or "").strip()
    if not api_key:
        raise TranslationUnavailable(
            "No translation key is configured for this deployment. Set DEEPL_API_KEY."
        )

    response = client.post(
        f"https://{_deepl_host(api_key)}/v2/translate",
        headers={"Authorization": f"DeepL-Auth-Key {api_key}"},
        json={"text": chunks, "target_lang": _DEEPL_TARGET},
        timeout=settings.translation_timeout_seconds,
    )
    if response.status_code != 200:
        log_ctx(
            logger,
            logging.WARNING,
            "translation refused",
            provider="deepl",
            status=response.status_code,
        )
        raise TranslationUnavailable(
            _DEEPL_ERRORS.get(
                response.status_code,
                "The translation service refused the request. Try again in a minute.",
            )
        )

    try:
        translations = response.json()["translations"]
        texts = [str(item["text"]) for item in translations]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise TranslationUnavailable(
            "The translation service answered in a shape this app does not understand."
        ) from exc

    if not any(text.strip() for text in texts):
        raise TranslationUnavailable("The translation service returned nothing.")

    detected = next(
        (str(item.get("detected_source_language") or "") for item in translations if item),
        "",
    )
    return ProviderResult(texts=texts, detected_source=detected or None)


#: Longest text each provider accepts in one request. Enforced as a ceiling on
#: TRANSLATION_MAX_CHUNK_CHARS so an operator cannot configure a value the
#: provider will reject - MyMemory's 500 is a hard limit that answers 403 in a
#: 200 response, which is a confusing failure to debug from the outside.
MAX_CHUNK_CHARS_BY_PROVIDER = {"google_free": 4000, "mymemory": 500, "deepl": 4000}

#: Provider name -> implementation. Adding a keyed provider is an entry here and
#: a changed TRANSLATION_PROVIDER; no caller changes.
_PROVIDERS = {"google_free": _google_free, "mymemory": _mymemory, "deepl": _deepl}


def translate(
    text: str,
    source_language: str | None,
    settings: Settings | None = None,
    client: httpx.Client | None = None,
) -> Translated:
    """Translate ``text`` into English. Raises TranslationUnavailable, never returns junk.

    ``source_language`` is the *stored* value, normalised here rather than by the
    caller so every entry point normalises identically.
    """
    settings = settings or get_settings()
    body = (text or "").strip()
    if not body:
        raise TranslationUnavailable("This notice has no description to translate.")

    # The same three cases as `needs_translation`, resolved the same way and in
    # the same order, so the button and the request it sends can never disagree
    # about what this notice is. A stored foreign code wins; otherwise the text
    # decides; and a text that reads as English is refused rather than round-
    # tripped through a translator that would hand back approximately itself.
    stored = normalise_language(source_language)
    if stored is not None and stored != TARGET_LANGUAGE:
        source: str | None = stored
    else:
        detection = language_detect.detect(body)
        if detection is None:
            raise TranslationUnavailable("This notice has no description to translate.")
        if detection.is_english or (stored == TARGET_LANGUAGE and not detection.is_confident):
            # The second half mirrors `needs_translation` case 3 exactly. If the
            # two ever disagree, the button appears and pressing it 409s, which
            # is the worst of both - so they are written to be read together.
            raise TranslationUnavailable("This notice is already in English.")
        # None here means "not confident enough to name it" and becomes the
        # provider's autodetect, not a guess this app commits to.
        source = language_detect.source_language_for(body)

    provider_name = settings.translation_provider
    provider = _PROVIDERS.get(provider_name)
    if provider is None:
        raise TranslationUnavailable(
            f"TRANSLATION_PROVIDER is set to '{provider_name}', which this build does not know."
        )

    # The provider's own cap wins over the setting: a chunk it will refuse is a
    # 403 inside a 200, which reads as "the service is broken" from the outside.
    limit = min(
        settings.translation_max_chunk_chars,
        MAX_CHUNK_CHARS_BY_PROVIDER.get(provider_name, settings.translation_max_chunk_chars),
    )
    chunks = chunk_text(body, limit)
    owned = client is None
    http = client or httpx.Client(follow_redirects=True)
    try:
        result = provider(chunks, source, settings, http)
    except httpx.HTTPError as exc:
        # The cause is logged and not shown: a transport error string names
        # hosts and ports, which is not a sentence for a reader.
        log_ctx(logger, logging.WARNING, "translation failed", provider=provider_name, error=str(exc))
        raise TranslationUnavailable(
            "Could not reach the translation service. Check the connection and try again."
        ) from exc
    finally:
        if owned:
            http.close()

    joined = " ".join(part.strip() for part in result.texts if part.strip())

    # What to *report* as the source, most-trustworthy first. The provider's own
    # detection wins when it offered one, because that is what it actually
    # translated from - anything else would caption the text with a language it
    # was not read as. Only then the code the request was made with, and then
    # nothing; the empty string is a real answer here, see `Translated`.
    #
    # The order matters more than it looks. Written the other way round, a DeepL
    # translation of a German notice stored by PNCP as `pt` would be captioned
    # "translated from Portuguese" - `deepl` ignores the source it is handed
    # precisely because its own detection is better.
    reported = normalise_language(result.detected_source) or source or ""
    log_ctx(
        logger,
        logging.INFO,
        "translated",
        provider=provider_name,
        source=source or "autodetect",
        detected=result.detected_source,
        chunks=len(chunks),
        chars=len(body),
    )
    return Translated(
        text=joined,
        source_language=reported,
        target_language=TARGET_LANGUAGE,
        provider=provider_name,
    )
