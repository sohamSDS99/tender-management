"""What language is this text actually written in?

This module exists because the `language` column lies, and it lies most often
for exactly the notices a reader most needs translated.

**The measurement that produced this file.** 413 notices were fetched live from
all seven reachable sources and every description was classified. TED — the
whole of European procurement — stores `language='eng'` on **100%** of its
notices, and 11 of its 15 descriptions were German, French or Dutch. The stored
value was not merely inconsistent, it was confidently wrong, so `needs_translation`
answered False for every German notice TED has ever published.

The cause is worth writing down because it is not a bug in the connector.
TED machine-translates a notice's *title* into all 24 EU languages, so the
`notice-title` map always contains an `eng` key, and `TedConnector._normalize`
reads the notice's language off that map. The `description-lot` map carries only
the buyer's own language. The stored value therefore describes the title
accurately and the description not at all — and `needs_translation` compares it
against the description. The connectors are frozen (see CLAUDE.md), so the
correction belongs here, on the read side, where it also repairs every notice
already stored without a migration or a re-ingest.

**Why a detector rather than a better column.** The language of a piece of text
is a property of the text. Every other answer — a column, a per-source table, a
country lookup — is a cache of that property, and this codebase has now been
bitten twice by a stale cache of it. Reading it from the text is the only version
that cannot drift from the thing it describes.

**The one non-obvious thing about py3langid: lowercase first.** The model is
trained on natural-case text, so an ALL-CAPS English procurement notice is not
English to it. Two real CanadaBuys notices — `THIS BID SOLICITATION CANCELS AND
SUPERSEDES...` and `*** THIS AMENDS THE PREVIOUSLY POSTED NOTICE...` — classified
as **Maltese (0.91)** and **Xhosa (0.64)**. Both become English at 1.00 when
lowercased, and lowercasing changed 4 classifications across the corpus, every
one of them from wrong to right. Capitalised headers are the house style of
procurement writing, so this is the common case here, not an edge case.

Restricting the model to the ~60 languages these eight sources can plausibly emit
was tried and made **zero** difference across all 413 notices, so it is not done:
it would be a hand-maintained list that goes stale the first time a feed changes,
bought for no measured accuracy.

**Known limitation, measured and deliberately not tuned away.** On a very short
text the confidence score is not a reliable guide to *which* language it is -
`Fassade Dämmung` classifies as Swedish at **0.9966**, and it is German. Both
real short descriptions in the corpus (`Küchentechnik Wartung`,
`Innenputz-/ Malerarbeiten`) score 1.00 and are right, so there is no evidence
here to tune against and a length rule fitted to one invented string would be
worse than none. It matters less than it looks: the *button* only needs "not
English", which was correct in every case, and a wrong source code makes the
translation weak rather than wrong - MyMemory answered `Fassade Dämmung` with
`Fassade Dämmung`. If short-text notices ever become a real complaint, the fix
with evidence behind it is to prefer the provider's own detection below some
length, because MyMemory named `Küchentechnik Wartung` as German correctly when
this module was asked and could not.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - import cost is the whole point of deferring it
    from py3langid.langid import LanguageIdentifier

#: Below this many letters there is not enough signal to classify, and there is
#: also nothing worth translating. Two real TED notices carry `-` and `...` as
#: their entire description; both classified as English at 0.17, which is the
#: detector correctly reporting that it has been asked an unanswerable question.
#: They must not get a button — not because they might be English, but because
#: there is no text to send.
MIN_LETTERS = 8

#: Above this, the named language is trusted enough to hand to a translation
#: provider as the source. Below it the text is still used to *offer* the button
#: — that decision only needs "not English" — but the provider is asked to detect
#: the language itself rather than being told a guess. D33's warning stands:
#: picking Portuguese for a notice that is actually Spanish produces confident
#: nonsense, and the reader cannot tell. Every correct non-English detection in
#: the 413-notice corpus scored 1.00; the wrong ones scored 0.64 and 0.91.
CONFIDENT = 0.90

#: Only the first of these characters is examined per notice. Language is a
#: property of the whole text but classification saturates long before the end of
#: a 20,000-character World Bank notice, and the longest stored description is
#: 20,000 characters.
MAX_SAMPLE_CHARS = 3000

#: The first word of `unicodedata.name(char)` for scripts English does not use.
#: A floor under the answer, not a replacement for it: a Greek notice is not
#: English even if the model is unsure which language it is.
#:
#: These are the *character names*, not the script names, and the two differ
#: exactly once in this list - a Chinese character is `CJK UNIFIED IDEOGRAPH-5E02`,
#: so the entry is `CJK` and `HAN` matches nothing at all. Every prefix here was
#: read off `unicodedata.name` rather than assumed; `HAN` was assumed, and Chinese
#: notices fell straight through the guard until a test written from real text
#: caught it.
_NON_LATIN_SCRIPTS = (
    "GREEK",
    "CYRILLIC",
    "ARABIC",
    "HEBREW",
    "CJK",
    "HIRAGANA",
    "KATAKANA",
    "HANGUL",
    "THAI",
    "DEVANAGARI",
    "BENGALI",
    "TAMIL",
    "TELUGU",
    "GEORGIAN",
    "ARMENIAN",
    "ETHIOPIC",
    "KHMER",
    "LAO",
    "MYANMAR",
    "SINHALA",
)

#: How much of the text must be non-Latin before the script decides. A Latin
#: notice quoting one Greek unit symbol is still a Latin notice.
_NON_LATIN_SHARE = 0.30


@dataclass(frozen=True)
class Detection:
    """What the text says it is, and how strongly.

    ``code`` is ISO 639-1, the same alphabet `translator.normalise_language`
    produces, so the two are interchangeable at every call site.
    """

    code: str
    confidence: float

    @property
    def is_english(self) -> bool:
        return self.code == "en"

    @property
    def is_confident(self) -> bool:
        return self.confidence >= CONFIDENT


@lru_cache(maxsize=1)
def _identifier() -> LanguageIdentifier:
    """The model, loaded once, on first use rather than at import.

    Roughly 2 MB of pickled n-gram tables. Loading it at import would put that
    cost on every `python -m app.accounts_cli`, every alembic run and every test
    collection, none of which classify anything.
    """
    from py3langid.langid import MODEL_FILE, LanguageIdentifier

    return LanguageIdentifier.from_pickled_model(MODEL_FILE, norm_probs=True)


def _letters(text: str) -> int:
    return sum(1 for char in text if char.isalpha())


def is_non_latin(text: str) -> bool:
    """Whether the text is substantially written in a script English does not use.

    Certainty, not a guess: if a third of the letters are Greek or Cyrillic or
    Han, no classifier result can make this English.
    """
    scripts = 0
    total = 0
    for char in text:
        if not char.isalpha():
            continue
        total += 1
        try:
            name = unicodedata.name(char)
        except ValueError:  # pragma: no cover - unnamed codepoint
            continue
        if name.split()[0] in _NON_LATIN_SCRIPTS:
            scripts += 1
    return total > 0 and (scripts / total) >= _NON_LATIN_SHARE


def detect(text: str | None) -> Detection | None:
    """Classify ``text``, or None when there is too little of it to judge.

    None means "unanswerable", never "English" — callers must not read a missing
    answer as a reason to hide the button, only as a reason there is nothing to
    translate.
    """
    body = (text or "").strip()
    if _letters(body) < MIN_LETTERS:
        return None
    sample = body[:MAX_SAMPLE_CHARS]

    # Lowercased for the reason in the module docstring. This single call is the
    # difference between two English CanadaBuys notices reading as Maltese and
    # Xhosa, and reading as English at 1.00.
    code, confidence = _identifier().classify(sample.lower())
    detection = Detection(code=str(code), confidence=float(confidence))

    if detection.is_english and is_non_latin(sample):
        # The script overrules the model. Reported at the confidence the model
        # gave so a caller can still see it was not sure which language it is.
        return Detection(code="und", confidence=detection.confidence)
    return detection


def is_probably_english(text: str | None) -> bool:
    """Whether ``text`` reads as English.

    Deliberately asymmetric, and this is the whole point of the module. It
    answers True only when the detector positively says English; anything else —
    another language, a non-Latin script, a text too short to judge — is False.

    A caller deciding whether to *offer* a translation wants exactly that bias:
    an extra button on an English notice costs one click, a missing button on a
    German one costs the reader the notice. Callers that must not act on
    "too short to judge" check `detect(...) is None` themselves.
    """
    detection = detect(text)
    return detection is not None and detection.is_english


def source_language_for(text: str | None) -> str | None:
    """The code to hand a translation provider as the source, or None for autodetect.

    None is a real answer and the providers take it: MyMemory accepts the literal
    `Autodetect` as a langpair source and returns the language it found, so an
    unsure classification becomes the provider's problem rather than a confident
    guess baked into a cached translation nobody can tell is wrong.
    """
    detection = detect(text)
    if detection is None or detection.is_english or not detection.is_confident:
        return None
    if detection.code == "und":
        return None
    return detection.code
