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

2. **Perform the translation** (`translate`), behind one function so the
   provider is configuration rather than code - the same shape
   `app/services/mailer.py` uses for SMTP.

**On the providers, and why there are two keyless ones.** Both are
undocumented, unversioned and rate-limited by IP, chosen by the operator knowing
that. The difference is measured, not theoretical:

- ``mymemory`` is the **default**, because it is the only one that answers from a
  datacenter. It caps a request at 500 characters and reports failure as a body
  field inside an HTTP 200.
- ``google_free`` gives better English on concatenated legal prose and takes
  8,000 characters at a time, but answers **429 to Railway's egress IP** whatever
  headers are sent - so it is the right choice from a laptop or the LAN
  deployment, and useless in production.

Every translation is cached in `tender_translations`, so a notice is fetched once
and never again - that is what keeps the request count proportional to *new*
foreign notices a human actually opens, rather than to page views, and it is
what makes a keyless service with a daily character allowance workable at all.

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
    """One completed translation, and where it came from."""

    text: str
    source_language: str
    target_language: str
    provider: str


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

    Both halves matter. A notice with no description has nothing to translate,
    and an unknown language is left alone deliberately - offering a button that
    would send English text to a translator, or guess wrongly at Portuguese,
    is worse than offering nothing.
    """
    if not (description or "").strip():
        return False
    code = normalise_language(language)
    return code is not None and code != TARGET_LANGUAGE


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


def _google_free(chunks: list[str], source: str, settings: Settings, client: httpx.Client) -> list[str]:
    """Google's keyless endpoint. Returns one translated string per chunk.

    The response is a nested array, not an object: ``[[[translated, original,
    ...], ...], null, detected_source, ...]``. Several segments come back for a
    long chunk and they concatenate with no separator - the endpoint splits on
    sentence boundaries and keeps the trailing spaces inside each segment.
    """
    out: list[str] = []
    for chunk in chunks:
        response = client.get(
            "https://translate.googleapis.com/translate_a/single",
            params={"client": "gtx", "sl": source, "tl": TARGET_LANGUAGE, "dt": "t", "q": chunk},
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
        except (json.JSONDecodeError, IndexError, KeyError, TypeError) as exc:
            raise TranslationUnavailable(
                "The translation service answered in a shape this app does not understand."
            ) from exc
        if not translated.strip():
            raise TranslationUnavailable("The translation service returned nothing.")
        out.append(translated)
    return out


def _mymemory(chunks: list[str], source: str, settings: Settings, client: httpx.Client) -> list[str]:
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
    for chunk in chunks:
        params: dict[str, str] = {"q": chunk, "langpair": f"{source}|{TARGET_LANGUAGE}"}
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

        translated = str((payload.get("responseData") or {}).get("translatedText") or "")
        if not translated.strip():
            raise TranslationUnavailable("The translation service returned nothing.")
        out.append(translated)
    return out


#: Longest text each provider accepts in one request. Enforced as a ceiling on
#: TRANSLATION_MAX_CHUNK_CHARS so an operator cannot configure a value the
#: provider will reject - MyMemory's 500 is a hard limit that answers 403 in a
#: 200 response, which is a confusing failure to debug from the outside.
MAX_CHUNK_CHARS_BY_PROVIDER = {"google_free": 4000, "mymemory": 500}

#: Provider name -> implementation. Adding a keyed provider is an entry here and
#: a changed TRANSLATION_PROVIDER; no caller changes.
_PROVIDERS = {"google_free": _google_free, "mymemory": _mymemory}


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
    source = normalise_language(source_language)
    if source is None:
        raise TranslationUnavailable("This notice does not record which language it is in.")
    if source == TARGET_LANGUAGE:
        raise TranslationUnavailable("This notice is already in English.")

    body = (text or "").strip()
    if not body:
        raise TranslationUnavailable("This notice has no description to translate.")

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
        parts = provider(chunks, source, settings, http)
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

    joined = " ".join(part.strip() for part in parts if part.strip())
    log_ctx(
        logger,
        logging.INFO,
        "translated",
        provider=provider_name,
        source=source,
        chunks=len(chunks),
        chars=len(body),
    )
    return Translated(
        text=joined,
        source_language=source,
        target_language=TARGET_LANGUAGE,
        provider=provider_name,
    )
