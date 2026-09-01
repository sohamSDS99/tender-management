"""What language is this text in?

The module under test decides whether a Translate button appears, so the shape of
its answers matters more than the accuracy of any single one: it must never say
"English" when it means "I cannot tell", and it must distinguish "too short to
judge" from "judged, and it is English". Those two collapse into one another very
easily, and when they do the symptom is a missing button on a foreign notice -
silent, and indistinguishable from the feature being switched off.

The samples are real notice text wherever the language matters.
"""

from __future__ import annotations

import pytest

from app.services import language

# Real descriptions, verbatim from the feeds, kept short enough to read.
GERMAN = (
    "Die ausgeschriebenen Arbeiten umfassen sämtliche Metallbauarbeiten im äußeren "
    "Bereich im Bestand und für den Neubau."
)
FRENCH = (
    "Le présent marché a pour objet l’exécution du nettoyage des locaux de l’EFS "
    "Bretagne sur le site de Quimper."
)
PORTUGUESE = (
    "Aquisição de veículo automotor tipo van zero quilômetro, conforme condições e "
    "exigências estabelecidas no edital."
)
ENGLISH = (
    "The scope of the contract to be awarded consists of the design, engineering, "
    "procurement, installation and commissioning of an integrated heat system."
)


@pytest.mark.parametrize(
    "text,expected",
    [
        (GERMAN, "de"),
        (FRENCH, "fr"),
        (PORTUGUESE, "pt"),
        (ENGLISH, "en"),
        # Two words is enough when the words are distinctive. Both are real TED
        # descriptions in full - not excerpts - and both are why a length floor
        # tuned by intuition rather than by the corpus would lose real notices.
        ("Küchentechnik Wartung", "de"),
        ("Innenputz-/ Malerarbeiten", "de"),
    ],
)
def test_real_descriptions_are_identified(text, expected):
    detection = language.detect(text)
    assert detection is not None
    assert detection.code == expected


@pytest.mark.parametrize("text", ["", "   ", None, "-", "...", "1.500,00", "***"])
def test_text_with_nothing_in_it_is_unanswerable_not_english(text):
    """None means "cannot judge", and callers must not read it as "English".

    `-` and `...` are real TED descriptions. They classify as English at 0.17 if
    asked, which is the model politely answering an unanswerable question; the
    length floor is what stops that answer being mistaken for information.
    """
    assert language.detect(text) is None
    assert language.is_probably_english(text) is False


def test_is_probably_english_is_asymmetric_on_purpose():
    """True only for a positive reading of English. Everything else is False.

    A caller deciding whether to *offer* a translation wants this bias: an extra
    button on an English notice costs a click, a missing button on a German one
    costs the reader the notice.
    """
    assert language.is_probably_english(ENGLISH) is True
    assert language.is_probably_english(GERMAN) is False
    assert language.is_probably_english(FRENCH) is False
    assert language.is_probably_english(None) is False


@pytest.mark.parametrize(
    "text",
    [
        "Προμήθεια και εγκατάσταση συστήματος θέρμανσης για το δημοτικό κτίριο.",
        "Поставка оборудования для системы водоснабжения городского округа.",
        "المناقصة الخاصة بتوريد وتركيب أنظمة السلامة والصحة المهنية للمباني.",
        "市政府办公大楼消防安全系统的设计、供应与安装工程招标公告。",
        "都市計画道路の設計及び施工に関する一般競争入札の公告について。",
    ],
)
def test_a_script_english_does_not_use_is_never_english(text):
    """Certainty, not a guess, and it must not depend on the classifier being right.

    `is_non_latin` is a floor under the answer: whatever the model decides about
    which language this is, it is not English, and that is cheap to establish and
    impossible to get wrong.
    """
    assert language.is_non_latin(text) is True
    assert language.is_probably_english(text) is False


def test_a_latin_notice_quoting_a_greek_symbol_is_still_latin():
    """One unit symbol does not change the script of a notice.

    Without a share threshold, `Ω` or `µ` in a specification would flip an
    English notice to "not English" - and specifications are full of them.
    """
    assert language.is_non_latin("Supply of resistors rated at 12 Ω for the substation.") is False
    assert language.is_probably_english("Supply of resistors rated at 12 Ω for the substation.")


def test_all_caps_english_is_english():
    """The lowercasing guard. Remove the `.lower()` in `detect` and this turns red.

    Both strings are real CanadaBuys notices, and they classified as Maltese
    (0.91) and Xhosa (0.64) before it. Capitalised headers are the house style of
    procurement writing, so this is the common case, not a curiosity.
    """
    shouty = (
        "THIS BID SOLICITATION CANCELS AND SUPERSEDES PREVIOUS BID SOLICITATION "
        "NUMBER 30006201 DATED NOVEMBER 22, 2024 WITH A CLOSING OF DECEMBER 18."
    )
    amend = (
        "*** THIS AMENDS THE PREVIOUSLY POSTED NOTICE TO CHANGE THE CLOSING DATE TO "
        "SEPTEMBER 17, 2026. TIME AND CLOSING LOCATION REMAIN UNCHANGED ***"
    )
    assert language.detect(shouty).code == "en"
    assert language.detect(amend).code == "en"


def test_a_confident_foreign_reading_names_a_source_for_the_provider():
    assert language.source_language_for(GERMAN) == "de"
    assert language.source_language_for(FRENCH) == "fr"
    assert language.source_language_for(PORTUGUESE) == "pt"


def test_english_and_the_unknowable_ask_for_no_source_at_all():
    """None here means the provider detects it, not that this app guessed.

    D33's warning survives the amendment intact: picking Portuguese for a notice
    that is actually Spanish produces confident nonsense a reader cannot check.
    Handing the provider `Autodetect` is how the button can be offered on a text
    this app cannot name without also inventing a language for it.
    """
    assert language.source_language_for(ENGLISH) is None
    assert language.source_language_for("-") is None
    assert language.source_language_for(None) is None
    # Three content words and no function words: a real reading, but a weak one.
    assert language.detect("Cloud storage framework.").is_confident is False
    assert language.source_language_for("Cloud storage framework.") is None


def test_the_model_is_loaded_once_and_not_at_import():
    """Roughly 2 MB of n-gram tables, on first use rather than on every alembic run."""
    language._identifier.cache_clear()
    assert language._identifier.cache_info().currsize == 0
    language.detect(GERMAN)
    language.detect(FRENCH)
    assert language._identifier.cache_info().currsize == 1


def test_a_very_long_notice_is_sampled_rather_than_read_whole():
    """Classification saturates long before the end of a 20,000-character notice.

    The assertion is that the answer does not change, which is the only reason
    the sampling is safe to do.
    """
    long_notice = ENGLISH + (" " + ENGLISH) * 200
    assert len(long_notice) > language.MAX_SAMPLE_CHARS
    assert language.detect(long_notice).code == "en"
    assert language.detect(GERMAN + (" " + GERMAN) * 200).code == "de"


# --- "does the reader already have English?" is a different question ---------


BILINGUAL = (
    "Please be advised that late submissions will not be accepted or considered. "
    "This bid solicitation is Phase One of a two-phase procurement process, and only "
    "suppliers deemed qualified in Phase One will be invited to submit a financial bid.\n\n"
    "Veuillez noter que les soumissions tardives ne seront ni acceptées ni prises en "
    "compte. La présente demande de soumissions constitue la première étape d’un "
    "processus d’approvisionnement en deux étapes, et seuls les fournisseurs qualifiés "
    "seront invités à soumettre une offre financière."
)


def test_a_bilingual_notice_reads_as_french_but_contains_english():
    """The two questions, and the gap between them that reached production.

    Classified whole, this is French - French carries more signal per character
    than English does, so it wins on a 50/50 text. 127 of 256 stored CanadaBuys
    notices did exactly this and grew a Translate button they did not need.
    """
    assert detect_code(BILINGUAL) == "fr"
    assert language.contains_english(BILINGUAL) is True
    assert 0.3 < language.english_share(BILINGUAL) < 0.7


def detect_code(text: str) -> str:
    return language.detect(text).code


@pytest.mark.parametrize("text,expected", [(GERMAN, False), (FRENCH, False), (ENGLISH, True)])
def test_a_single_language_notice_is_all_or_nothing(text, expected):
    assert language.contains_english(text) is expected


def test_the_share_is_weighted_by_length_not_by_segment_count():
    """A two-word English header must not outvote three thousand characters of French.

    Counting segments would make this notice 50% English on a 30-character
    header, and the reader would be left with a wall of French and no button.
    """
    with_header = f"NOTICE OF PROPOSED PROCUREMENT\n\n{FRENCH * 8}"

    assert language.contains_english(with_header) is False
    assert language.english_share(with_header) < language.ENGLISH_PRESENT_SHARE


def test_a_bilingual_notice_written_as_one_block_is_still_caught():
    """No blank line to split on, so a long paragraph is sub-split into windows.

    Without that, a bilingual notice that happens to use single newlines would
    read as whatever language won the whole-text vote.
    """
    one_block = BILINGUAL.replace("\n\n", " ") * 3

    assert len(language.segments(one_block)) > 1
    assert language.contains_english(one_block) is True


def test_a_short_foreign_notice_is_not_chopped_up_looking_for_english():
    """Below the split threshold a paragraph is left whole.

    Slicing `Küchentechnik Wartung` into windows would only manufacture noise,
    and these two are exactly the notices D34 exists to give a button to.
    """
    assert language.segments("Küchentechnik Wartung") == ["Küchentechnik Wartung"]
    assert language.contains_english("Küchentechnik Wartung") is False
    assert language.contains_english("Innenputz-/ Malerarbeiten") is False
