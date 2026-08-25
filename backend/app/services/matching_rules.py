"""The parts of the relevance profile an operator may tune.

``config/relevance_profiles.yaml`` is 866 lines of weights, bands, phrase lists
and regexes, and its comments are load-bearing: they document the matching
contract every phrase in the file has to satisfy. So the file is **never
rewritten**. Overrides live in ``app_settings`` and are merged over it at load,
on the same rail as the sweep decision (D21) - which means the file stays
authoritative and readable, "reset to defaults" is a row deletion, and no
comment-preserving YAML writer has to be added as a dependency.

What is exposed is the ~15% people actually tune. ``patterns:`` regexes stay in
the file; they are the sharp edge and they are rarely touched.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.logging_config import log_ctx
from app.models import AppSetting, Tender, utcnow
from app.services.relevance import RelevanceEngine, load_config, normalize

logger = logging.getLogger(__name__)

KEY_RULES_OVERRIDE = "matching.overrides"

WEIGHT_KEYS = ("topic", "product_fit", "procurement_intent")
#: Highest band first. Each must stay strictly above the next, or a score falls
#: into two bands at once and the fit status it reports becomes arbitrary.
BAND_ORDER = ("excellent_fit", "good_fit", "possible_fit", "weak_fit")
PHRASE_TIERS = ("strong", "medium", "weak")


class InvalidRules(ValueError):
    """Raised with a message written for the person who typed it."""


def normalise_phrase(raw: str) -> str:
    """The file's matching contract, applied to what someone typed.

    Delegates to the engine's own normaliser rather than reimplementing it: two
    implementations of this would drift, and the drift would silently stop
    phrases matching.
    """
    return normalize(raw).strip()


def _stored(db: Session) -> dict[str, Any]:
    row = db.get(AppSetting, KEY_RULES_OVERRIDE)
    if row is None or not row.value:
        return {}
    try:
        parsed = json.loads(row.value)
    except json.JSONDecodeError:
        # A hand-edited row must not take the scoring engine down with it.
        log_ctx(logger, logging.WARNING, "matching overrides unreadable", action="ignored")
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _validate(overrides: dict[str, Any], base: dict[str, Any]) -> dict[str, Any]:
    clean: dict[str, Any] = {}

    weights = overrides.get("weights")
    if weights:
        unknown = set(weights) - set(WEIGHT_KEYS)
        if unknown:
            raise InvalidRules(f"Unknown weight: {', '.join(sorted(unknown))}.")
        values = {k: float(weights.get(k, base.get("weights", {}).get(k, 0))) for k in WEIGHT_KEYS}
        if any(v < 0 for v in values.values()):
            raise InvalidRules("A weight cannot be negative.")
        total = round(sum(values.values()), 4)
        if abs(total - 1.0) > 0.005:
            raise InvalidRules(f"The three weights must add up to 1.00. They add up to {total:.2f}.")
        clean["weights"] = values

    bands = overrides.get("bands")
    if bands:
        merged = {**base.get("bands", {}), **bands}
        for name, value in bands.items():
            if not isinstance(value, int | float) or not 0 <= value <= 100:
                raise InvalidRules(f"{name} must be a score between 0 and 100.")
        ordered = [merged.get(name) for name in BAND_ORDER if merged.get(name) is not None]
        if any(a <= b for a, b in zip(ordered, ordered[1:], strict=False)):
            raise InvalidRules(
                "Bands must descend: excellent above good, good above possible, possible above weak."
            )
        clean["bands"] = {k: int(v) for k, v in bands.items()}

    profiles = overrides.get("profiles")
    if profiles:
        known = set(base.get("profiles", {}))
        out: dict[str, Any] = {}
        for key, tiers in profiles.items():
            if key not in known:
                raise InvalidRules(f"'{key}' is not a profile in the relevance file.")
            entry: dict[str, list[str]] = {}
            for tier, phrases in tiers.items():
                if tier not in PHRASE_TIERS:
                    raise InvalidRules(f"'{tier}' is not one of strong, medium, weak.")
                seen: list[str] = []
                for phrase in phrases:
                    norm = normalise_phrase(str(phrase))
                    if norm and norm not in seen:
                        seen.append(norm)
                entry[tier] = seen
            out[key] = entry
        clean["profiles"] = out

    return clean


def save_overrides(db: Session, overrides: dict[str, Any]) -> dict[str, Any]:
    """Validate and store. Returns what was actually stored."""
    base = load_config()
    clean = _validate(overrides, base)
    row = db.get(AppSetting, KEY_RULES_OVERRIDE)
    payload = json.dumps(clean)
    if row is None:
        db.add(AppSetting(key=KEY_RULES_OVERRIDE, value=payload, updated_at=utcnow()))
    else:
        row.value = payload
        row.updated_at = utcnow()
    db.commit()
    log_ctx(logger, logging.INFO, "matching rules changed", sections=",".join(sorted(clean)) or "none")
    return clean


def clear_overrides(db: Session) -> None:
    """Hand control back to the file."""
    row = db.get(AppSetting, KEY_RULES_OVERRIDE)
    if row is not None:
        db.delete(row)
        db.commit()
    log_ctx(logger, logging.INFO, "matching rules reset", action="file defaults restored")


def apply_overrides(db: Session, config: dict[str, Any]) -> dict[str, Any]:
    """Merge stored overrides over a loaded config.

    Shallow per section on purpose: an override names the individual weights,
    bands or phrase tiers it changes, and everything it does not name is left
    exactly as the file had it.
    """
    overrides = _stored(db)
    if not overrides:
        return config

    merged = dict(config)
    if "weights" in overrides:
        merged["weights"] = {**config.get("weights", {}), **overrides["weights"]}
    if "bands" in overrides:
        merged["bands"] = {**config.get("bands", {}), **overrides["bands"]}
    if "profiles" in overrides:
        profiles = {k: dict(v) for k, v in config.get("profiles", {}).items()}
        for key, tiers in overrides["profiles"].items():
            if key in profiles:
                profiles[key] = {**profiles[key], **tiers}
        merged["profiles"] = profiles
    return merged


def read_rules(db: Session) -> dict[str, Any]:
    """The curated subset, with overrides already applied - what the UI renders."""
    config = apply_overrides(db, load_config())
    weights = config.get("weights", {})
    return {
        "weights": {k: weights.get(k) for k in WEIGHT_KEYS},
        "bands": dict(config.get("bands", {})),
        "profiles": [
            {
                "key": key,
                "label": profile.get("label", key.replace("_", " ")),
                **{tier: list(profile.get(tier, []) or []) for tier in PHRASE_TIERS},
            }
            for key, profile in config.get("profiles", {}).items()
        ],
        "overridden": sorted(_stored(db)),
    }


# --- the engine the scoring paths actually use -----------------------------
#
# Built from the file *plus* overrides, and cached because scoring a sweep asks
# for it once per notice. The fingerprint is the stored row itself, so a saved
# change invalidates it without anyone remembering to call a clear function.
_cache: tuple[str, RelevanceEngine] | None = None


def engine_for(db: Session) -> RelevanceEngine:
    """The relevance engine with operator overrides applied."""
    global _cache
    row = db.get(AppSetting, KEY_RULES_OVERRIDE)
    fingerprint = f"{row.value}|{row.updated_at.isoformat()}" if row else ""
    if _cache is not None and _cache[0] == fingerprint:
        return _cache[1]
    engine = RelevanceEngine(apply_overrides(db, load_config()))
    _cache = (fingerprint, engine)
    return engine


def reset_engine_cache() -> None:
    """Drop the cached engine, for a file edit the fingerprint cannot see."""
    global _cache
    _cache = None


# --- what would change -----------------------------------------------------
#
#: Above this many stored notices the preview samples rather than scoring the
#: whole corpus. Scoring 470 twice is nothing; scoring 200,000 twice inside a
#: request is not, and a preview that times out is worse than an estimate.
PREVIEW_LIMIT = 5000


def preview(db: Session, overrides: dict[str, Any]) -> dict[str, Any]:
    """Score the corpus under candidate rules without storing anything.

    Answers the question a re-score actually raises - "what will move?" - before
    it moves. Nothing here writes: the candidate engine is built from a merged
    config held in memory and thrown away.
    """
    base = load_config()
    clean = _validate(overrides, base)

    current = engine_for(db)
    merged = dict(base)
    if "weights" in clean:
        merged["weights"] = {**base.get("weights", {}), **clean["weights"]}
    if "bands" in clean:
        merged["bands"] = {**base.get("bands", {}), **clean["bands"]}
    if "profiles" in clean:
        profiles = {k: dict(v) for k, v in base.get("profiles", {}).items()}
        for key, tiers in clean["profiles"].items():
            if key in profiles:
                profiles[key] = {**profiles[key], **tiers}
        merged["profiles"] = profiles
    candidate = RelevanceEngine(apply_overrides(db, merged))

    total = db.execute(select(func.count(Tender.id))).scalar_one()
    rows = db.execute(select(Tender).limit(PREVIEW_LIMIT)).scalars().all()

    good_now = current.bands.get("good_fit", 70)
    good_next = candidate.bands.get("good_fit", good_now)

    changed = crossing_up = crossing_down = 0
    for row in rows:
        before = current.score_obj(row)
        after = candidate.score_obj(row)
        if before.relevance_score != after.relevance_score:
            changed += 1
        was_good = before.relevance_score >= good_now
        is_good = after.relevance_score >= good_next
        if is_good and not was_good:
            crossing_up += 1
        elif was_good and not is_good:
            crossing_down += 1

    return {
        "changed": changed,
        "crossing_up": crossing_up,
        "crossing_down": crossing_down,
        "examined": len(rows),
        "total": total,
        #: True when the corpus was sampled, so the UI can say "about" rather
        #: than presenting an estimate as a count.
        "sampled": total > len(rows),
        "good_fit_band": good_next,
    }
