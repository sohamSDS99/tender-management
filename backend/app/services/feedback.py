"""Reviewer verdicts, and the patterns learned from them.

The relevance engine scores a notice against phrases somebody wrote down. This
is the other direction: patterns nobody wrote down, derived from what a reviewer
actually rejected. The two are kept strictly apart - **nothing here can move a
relevance score.** What it can do is *hide* a notice, which is reversible, is
attributable to named patterns, and is explained in the reviewer's own words.

How it learns
-------------
One token log-odds table, built in memory from the marked corpus and cached:

    weight(t) = log P(t | you rejected it) - log P(t | everything else)

Both halves matter. Comparing rejects against the *rest of the corpus* rather
than against the notices marked relevant is what makes this work from the fifth
mark rather than the five-hundredth: a word common everywhere ("contract",
"the", "services") appears just as often in both halves, so its weight lands
near zero and it drops out on its own. No stop-word list, no tuning, no
dependency - and every surviving pattern is a phrase a human can read and argue
with, which is the only kind of learning this product is allowed to do (D27).

Three floors stop it from over-reaching, and they are the whole safety story:

* ``MIN_MARKS`` - under five rejections it predicts nothing at all;
* ``MIN_DOC_FREQ`` - a pattern must appear in three separate rejections, so one
  unusual notice cannot invent one;
* the protection rule - any token appearing in a notice marked *relevant* is
  struck out entirely. A pattern present in something the team said yes to must
  never be able to hide anything.
"""

from __future__ import annotations

import logging
import math
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.logging_config import log_ctx
from app.models import Tender, TenderFeedback, utcnow
from app.models.tender_feedback import IRRELEVANT, RELEVANT, VERDICTS
from app.services.relevance import normalize

logger = logging.getLogger(__name__)

#: Rejections needed before the learner predicts anything. Under this it stays
#: silent and only explicit marks hide a notice - four rejections is an opinion,
#: not a pattern.
MIN_MARKS = 5

#: Separate rejected notices a token must appear in to count as a pattern. Stops
#: one notice's vocabulary (a buyer's name, a reference number) becoming a rule.
MIN_DOC_FREQ = 3

#: Log-odds floor: roughly "four times more common in rejects than elsewhere".
MIN_WEIGHT = 1.4

#: Matched weight at which a notice is hidden. Two strong patterns, or three
#: middling ones.
HIDE_AT = 4.0

#: ...and at least one of them must clear this on its own.
#:
#: Measured, not guessed. Without it, 611 real notices under eight marks hid
#: three whose only evidence was Brazilian procurement boilerplate -
#: "equipamentos", "especializada para" - summing past HIDE_AT while no single
#: phrase said anything. The verdicts happened to be right, but the *reason*
#: shown on the card was thin, and a hide justified by nothing a reader would
#: accept on its own is the one thing this design cannot afford. Because the
#: reason displayed is the strongest match, this makes the explanation as strong
#: as the rule that produced it.
STRONG_AT = 3.0

#: How many matched patterns can contribute to one notice's total, strongest
#: first. Without a cap a long description accumulates a hide from many weak
#: signals, which is exactly the unexplainable behaviour this design refuses.
MAX_MATCHES = 6

#: Patterns kept in the model, strongest first. Well past what a corpus this
#: size produces; it bounds memory rather than expressing a belief.
MAX_PATTERNS = 500

#: A token must look like a word. Pure digits, reference numbers and single
#: letters carry no transferable meaning.
_WORDLIKE = re.compile(r"^[a-z][a-z0-9]{2,}$")


def tokens(title: str | None, description: str | None = None, buyer_name: str | None = None) -> set[str]:
    """The bag of unigrams and bigrams a notice is matched on.

    Uses the engine's own normaliser rather than a second one: accent folding
    and punctuation collapsing have to agree with the phrase matcher, or a
    learned pattern and a configured phrase would mean different things.

    Bigrams are here because they are what reads as a *pattern* - "laboratory
    furniture" is a reason, "furniture" is a coincidence - and because a pair is
    far less likely than a single word to be shared with a notice we want.
    """
    words = [
        w
        for w in normalize(" ".join(x for x in (title, description, buyer_name) if x)).split()
        if _WORDLIKE.match(w)
    ]
    out = set(words)
    out.update(f"{a} {b}" for a, b in zip(words, words[1:], strict=False))
    return out


@dataclass(frozen=True)
class Pattern:
    """One learned phrase, with the evidence for it."""

    phrase: str
    weight: float
    #: Rejected notices it appears in, and notices in the rest of the corpus.
    #: Shown on screen: a pattern nobody can audit is a pattern nobody trusts.
    marked: int
    elsewhere: int


@dataclass
class Model:
    """A built log-odds table, plus why it is or is not in force."""

    patterns: dict[str, Pattern] = field(default_factory=dict)
    marks_irrelevant: int = 0
    marks_relevant: int = 0
    corpus: int = 0

    @property
    def active(self) -> bool:
        """Whether it may hide anything. Fewer marks than MIN_MARKS: never."""
        return self.marks_irrelevant >= MIN_MARKS and bool(self.patterns)

    def ranked(self) -> list[Pattern]:
        return sorted(self.patterns.values(), key=lambda p: (-p.weight, p.phrase))

    def match(self, bag: set[str]) -> tuple[float, list[Pattern]]:
        """Total matched weight and the patterns behind it, strongest first.

        Returns nothing at all unless the strongest match stands on its own -
        see STRONG_AT. A pile of weak matches is not evidence, and reporting a
        total without a single defensible phrase behind it would let the card
        claim a reason it does not have.
        """
        if not self.active:
            return 0.0, []
        hits = sorted(
            (self.patterns[t] for t in bag & self.patterns.keys()),
            key=lambda p: (-p.weight, p.phrase),
        )[:MAX_MATCHES]
        if not hits or hits[0].weight < STRONG_AT:
            return 0.0, []
        return sum(p.weight for p in hits), hits


def _reason(pattern: Pattern) -> str:
    """A pattern written as a sentence, in the same voice as the engine's own."""
    return (
        f"'{pattern.phrase}' appears in {pattern.marked} notices you marked not relevant"
        f"{f' and only {pattern.elsewhere} others' if pattern.elsewhere else ' and nothing else'}"
    )


def build(db: Session) -> Model:
    """Learn from every stored verdict. Reads only; writes nothing."""
    verdicts = {row.tender_id: row.verdict for row in db.execute(select(TenderFeedback)).scalars().all()}
    rejected = {tid for tid, v in verdicts.items() if v == IRRELEVANT}
    kept = {tid for tid, v in verdicts.items() if v == RELEVANT}

    in_rejects: Counter[str] = Counter()
    in_rest: Counter[str] = Counter()
    protected: set[str] = set()
    n_rejects = n_rest = 0

    # One pass, streaming: the corpus is the background class, so it all has to
    # be read - but only the counters are kept, never the rows.
    n_kept = 0
    for row in db.execute(select(Tender)).scalars():
        bag = tokens(row.title, row.description, row.buyer_name)
        if row.id in rejected:
            n_rejects += 1
            in_rejects.update(bag)
        else:
            n_rest += 1
            in_rest.update(bag)
        if row.id in kept:
            n_kept += 1
            protected |= bag

    # Counted from the notices actually present, not from the size of the
    # verdict table, and the difference is not academic. `--seed-reset` deletes
    # tenders with a Core `delete()`, and SQLite does not enforce ON DELETE
    # CASCADE unless foreign keys are switched on - so a verdict can outlive its
    # notice. Read off the table, an orphan would inflate the mark count and
    # could switch the learner on with marks that no longer refer to anything.
    # Read off the corpus, an orphan is simply ignored.
    model = Model(marks_irrelevant=n_rejects, marks_relevant=n_kept, corpus=n_rejects + n_rest)
    if n_rejects == 0:
        return model

    patterns: list[Pattern] = []
    for token, marked in in_rejects.items():
        if marked < MIN_DOC_FREQ or token in protected:
            continue
        elsewhere = in_rest[token]
        weight = math.log((marked + 0.5) / (n_rejects + 1)) - math.log((elsewhere + 0.5) / (n_rest + 1))
        if weight >= MIN_WEIGHT:
            patterns.append(Pattern(token, round(weight, 3), marked, elsewhere))

    patterns.sort(key=lambda p: (-p.weight, p.phrase))
    model.patterns = {p.phrase: p for p in patterns[:MAX_PATTERNS]}
    return model


# --- the cached model ------------------------------------------------------
#
# Same rail as matching_rules.engine_for: the fingerprint is the data itself, so
# a new verdict invalidates it without anyone remembering to call a clear
# function. The corpus size is in the fingerprint too, because the corpus is the
# background class - a sweep that stores 300 notices changes what "common
# elsewhere" means.
_cache: tuple[tuple, Model] | None = None


def _fingerprint(db: Session) -> tuple:
    marks, latest = db.execute(
        select(func.count(TenderFeedback.tender_id), func.max(TenderFeedback.updated_at))
    ).one()
    # The corpus is summarised by more than its size: an amended notice changes
    # what "common elsewhere" means without changing the count, and a count
    # alone would also let two different databases share a cache entry - which
    # is the whole test suite, running against a fresh in-memory database each
    # time and looking identical from here.
    corpus, newest_id, touched = db.execute(
        select(func.count(Tender.id), func.max(Tender.id), func.max(Tender.updated_at))
    ).one()
    return (
        marks,
        latest.isoformat() if latest else "",
        corpus,
        newest_id,
        touched.isoformat() if touched else "",
    )


def model_for(db: Session) -> Model:
    """The learned model, rebuilt only when a verdict or the corpus changed."""
    global _cache
    key = _fingerprint(db)
    if _cache is not None and _cache[0] == key:
        return _cache[1]
    model = build(db)
    _cache = (key, model)
    log_ctx(
        logger,
        logging.INFO,
        "feedback model built",
        marks=model.marks_irrelevant,
        patterns=len(model.patterns),
        active=str(model.active).lower(),
    )
    return model


def reset_model_cache() -> None:
    """Drop the cached model. For tests and for a corpus changed underneath us."""
    global _cache
    _cache = None


# --- prediction ------------------------------------------------------------


def predict(row: Tender, model: Model) -> tuple[bool, list[str]]:
    """Whether the learner would hide this notice, and why.

    A reviewer's own verdict always wins, in both directions: a notice marked
    relevant can never be auto-hidden, and one marked not relevant needs no
    prediction to justify hiding it.
    """
    verdict = row.feedback.verdict if row.feedback is not None else None
    if verdict is not None:
        return False, []
    total, hits = model.match(tokens(row.title, row.description, row.buyer_name))
    if total < HIDE_AT:
        return False, []
    return True, [_reason(p) for p in hits]


def apply_prediction(row: Tender, model: Model | None) -> bool:
    """Stamp the learner's call onto one row. True when it changed.

    Called from the ingest path for a new or amended notice and from a re-score,
    so the stored flag is never staler than the model that produced it.
    """
    before = (bool(row.auto_irrelevant), list(row.auto_irrelevant_reasons or []))
    hide, reasons = (False, []) if model is None else predict(row, model)
    row.auto_irrelevant = hide
    row.auto_irrelevant_reasons = reasons
    return before != (hide, reasons)


def apply_to_corpus(db: Session, model: Model | None = None) -> int:
    """Re-predict every stored notice. Returns how many changed.

    That count is the feature working out loud: mark one notice not relevant and
    the answer says how many others the system just stopped showing you.

    ponytail: a linear pass in Python over every row - milliseconds at a few
    thousand notices, which is the size this tool is built for. If the corpus
    reaches six figures, move it to the sweep and re-predict only what changed.
    """
    model = model if model is not None else model_for(db)
    changed = 0
    for row in db.execute(select(Tender)).scalars():
        if apply_prediction(row, model):
            changed += 1
    db.commit()
    return changed


# --- verdicts --------------------------------------------------------------


class UnknownVerdict(ValueError):
    """Raised with a message written for the person who sent it."""


def set_verdict(db: Session, tender_id: int, verdict: str, note: str | None = None) -> TenderFeedback:
    """Record or replace one verdict. Does not re-predict - the caller does.

    Written *through* ``tender.feedback`` rather than by adding a
    ``TenderFeedback`` row directly, and that is not a style choice. The
    relationship is eagerly loaded, so any Tender already in the session has
    ``feedback`` cached - as ``None`` if there was no verdict when it was read.
    Inserting the row behind the relationship's back leaves that cache stale,
    and the next ``predict`` on that object sees no verdict and cheerfully hides
    a notice a human has just marked relevant. Assigning to the attribute makes
    SQLAlchemy keep both sides in step, so the staleness cannot arise.
    """
    if verdict not in VERDICTS:
        raise UnknownVerdict(f"'{verdict}' is not one of {', '.join(VERDICTS)}.")
    tender = db.get(Tender, tender_id)
    if tender is None:
        raise LookupError(f"tender {tender_id} does not exist")
    row = tender.feedback
    if row is None:
        tender.feedback = TenderFeedback(verdict=verdict, note=note or None)
    else:
        row.verdict = verdict
        row.note = note or None
        row.updated_at = utcnow()
    db.commit()
    log_ctx(logger, logging.INFO, "verdict recorded", tender=tender_id, verdict=verdict)
    return tender.feedback


def clear_verdict(db: Session, tender_id: int) -> bool:
    """Forget a verdict. True if there was one.

    Detached through the relationship for the same reason as above; the
    delete-orphan cascade is what actually removes the row.
    """
    tender = db.get(Tender, tender_id)
    if tender is None or tender.feedback is None:
        return False
    tender.feedback = None
    db.commit()
    log_ctx(logger, logging.INFO, "verdict cleared", tender=tender_id)
    return True


# --- what "hidden" means ---------------------------------------------------


def marked_irrelevant_subquery():
    """`tenders.id NOT IN (...)`'s inner half, as a statement fragment.

    ``hidden`` is defined twice - here in SQL, and as a computed field on the
    API schema - because a filter has to run in the database and a response has
    to be assembled in Python. ``test_feedback.py`` asserts the two agree over
    the whole corpus, which is what keeps them from drifting.
    """
    return select(TenderFeedback.tender_id).where(TenderFeedback.verdict == IRRELEVANT)


# --- what the UI shows -----------------------------------------------------


def summary(db: Session, model: Model | None = None) -> dict[str, Any]:
    """Everything the Learned-patterns screen needs, in one read."""
    model = model if model is not None else model_for(db)
    hidden = db.execute(
        select(func.count(Tender.id)).where(
            (Tender.auto_irrelevant.is_(True)) | (Tender.id.in_(marked_irrelevant_subquery()))
        )
    ).scalar_one()
    auto = db.execute(select(func.count(Tender.id)).where(Tender.auto_irrelevant.is_(True))).scalar_one()
    return {
        "active": model.active,
        "marks_irrelevant": model.marks_irrelevant,
        "marks_relevant": model.marks_relevant,
        "marks_needed": max(0, MIN_MARKS - model.marks_irrelevant),
        "corpus": model.corpus,
        "hidden_total": hidden,
        "hidden_by_learning": auto,
        "hidden_by_hand": hidden - auto,
        "patterns": [
            {
                "phrase": p.phrase,
                "weight": p.weight,
                "marked": p.marked,
                "elsewhere": p.elsewhere,
            }
            for p in model.ranked()
        ],
        "thresholds": {
            "min_marks": MIN_MARKS,
            "min_doc_freq": MIN_DOC_FREQ,
            "min_weight": MIN_WEIGHT,
            "hide_at": HIDE_AT,
        },
    }
