"""Deterministic, explainable relevance scoring.

No AI/embedding service: everything is phrase + regex matching driven by
``config/relevance_profiles.yaml``.

Two internal subscores are combined into the final 0-100 score:

    final = 0.55 * topic_relevance + 0.30 * product/deployment fit
            + 0.15 * procurement intent

then hard caps (mandatory on-premises, chemical-purchase false positive, ...)
and a non-actionable multiplier (expired / cancelled) are applied.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from app.settings import get_settings

# --- fit statuses / deployment classes (the only allowed values) ------------
FIT_HIGH = "high_fit"
FIT_GOOD = "good_fit"
FIT_POSSIBLE = "possible_fit"
FIT_REVIEW = "manual_review"
FIT_NOT = "not_fit"
FIT_STATUSES = (FIT_HIGH, FIT_GOOD, FIT_POSSIBLE, FIT_REVIEW, FIT_NOT)

DEP_CLOUD_REQUIRED = "cloud_required"
DEP_CLOUD_PREFERRED = "cloud_preferred"
DEP_CLOUD_ALLOWED = "cloud_allowed"
DEP_UNSPECIFIED = "deployment_unspecified"
DEP_HYBRID = "hybrid"
DEP_ON_PREM = "mandatory_on_premises"
DEP_OFFLINE = "offline_or_air_gapped"
DEPLOYMENT_FITS = (
    DEP_CLOUD_REQUIRED,
    DEP_CLOUD_PREFERRED,
    DEP_CLOUD_ALLOWED,
    DEP_UNSPECIFIED,
    DEP_HYBRID,
    DEP_ON_PREM,
    DEP_OFFLINE,
)

_DEPLOYMENT_ORDER = (
    DEP_HYBRID,
    DEP_OFFLINE,
    DEP_ON_PREM,
    DEP_CLOUD_REQUIRED,
    DEP_CLOUD_PREFERRED,
    DEP_CLOUD_ALLOWED,
)
_DEPLOYMENT_BONUS = {
    # The scale must stay monotonic: an explicit "cloud is permitted" can never be
    # worth less than saying nothing at all. Raising DEP_UNSPECIFIED off the floor
    # (below) therefore lifts everything above it too - test_relevance.py's
    # test_unspecified_deployment_is_not_penalised pins exactly that ordering.
    DEP_CLOUD_REQUIRED: 35,
    DEP_CLOUD_PREFERRED: 33,
    DEP_CLOUD_ALLOWED: 30,
    DEP_HYBRID: 28,
    # A notice that says nothing about hosting is not a notice that ruled cloud
    # out, and the engine's own reason string says so ("Deployment model not
    # specified - cloud delivery is not excluded"). Scoring it 0, the same as
    # DEP_ON_PREM, contradicted that: notice summaries almost never state a
    # delivery model, so real notices were scored as though they had mandated
    # on-premises. Measured consequence: of the 469 TED notices this system's own
    # query returns over 12 months, *none* reached the score-70 floor that both
    # the Slack digest and the dashboard landing view filter on - the maximum was
    # 69 - so the view a reader lands on could only ever show SEED-* fixtures.
    # 26 is the smallest value that lets an on-target notice reach 70, and it is
    # bounded: at 26 a notice still needs topic_relevance == 100 (its clamp), i.e.
    # two or more strong SDS/EHS phrases, in practice in the title. 12 and 20 are
    # not enough to reach 70 at all; 35 would drop the required topic to ~95 and
    # start admitting near-misses.
    DEP_UNSPECIFIED: 26,
    DEP_ON_PREM: 0,
    DEP_OFFLINE: 0,
}
_NON_FIT_DEPLOYMENTS = (DEP_ON_PREM, DEP_OFFLINE)

_NON_ALNUM = re.compile(r"[^0-9a-z]+")


def compile_bounded(pattern: str) -> re.Pattern[str]:
    """Compile a YAML regex so it only matches on whole-word boundaries.

    The normalizer collapses punctuation to spaces, so without this a pattern
    like "g cloud" would match inside "tracking cloud".
    """
    return re.compile(rf"(?<![0-9a-z])(?:{pattern})(?![0-9a-z])")


def normalize(text: str | None) -> str:
    """Lower-case, fold accents, punctuation -> single spaces, space-padded.

    Padding makes a plain substring search whole-word safe.
    """
    if not text:
        return " "
    folded = unicodedata.normalize("NFKD", str(text)).encode("ascii", "ignore").decode("ascii")
    return " " + _NON_ALNUM.sub(" ", folded.lower()).strip() + " "


def _phrase(p: str) -> str:
    return normalize(p).strip()


@dataclass
class AcronymGate:
    """Outcome of ambiguous-acronym disambiguation."""

    blocked: set[str] = field(default_factory=set)
    review_flags: list[str] = field(default_factory=list)
    disqualifiers: list[str] = field(default_factory=list)
    cap_keys: list[str] = field(default_factory=list)


@dataclass
class Hit:
    phrase: str
    tier: str
    in_title: bool


@dataclass
class RelevanceResult:
    relevance_score: int
    relevance_category: str | None
    relevance_category_label: str | None
    fit_status: str
    deployment_fit: str
    relevance_reasons: list[str]
    disqualifiers: list[str]
    review_flags: list[str]
    topic_relevance_score: int
    product_fit_score: int
    procurement_intent_score: int
    is_actionable: bool
    profile_scores: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "relevance_score": self.relevance_score,
            "relevance_category": self.relevance_category,
            "fit_status": self.fit_status,
            "deployment_fit": self.deployment_fit,
            "relevance_reasons": self.relevance_reasons,
            "disqualifiers": self.disqualifiers,
            "review_flags": self.review_flags,
            "topic_relevance_score": self.topic_relevance_score,
            "product_fit_score": self.product_fit_score,
            "procurement_intent_score": self.procurement_intent_score,
            "is_actionable": self.is_actionable,
        }


class RelevanceEngine:
    """Compiled view of the YAML configuration."""

    def __init__(self, config: dict[str, Any]) -> None:
        self.raw = config
        self.weights = config["weights"]
        self.bands = config["bands"]
        self.caps = config["caps"]
        self.phrase_points = self.weights["phrase_points"]

        self.profiles = {
            name: {
                "label": spec.get("label", name),
                "strong": [_phrase(p) for p in spec.get("strong") or []],
                "medium": [_phrase(p) for p in spec.get("medium") or []],
                "weak": [_phrase(p) for p in spec.get("weak") or []],
            }
            for name, spec in config["profiles"].items()
        }
        pf = config["product_fit"]
        self.product_fit = {
            "points": pf["points"],
            "max": pf["max"],
            "strong": [_phrase(p) for p in pf.get("strong") or []],
            "medium": [_phrase(p) for p in pf.get("medium") or []],
            "weak": [_phrase(p) for p in pf.get("weak") or []],
        }
        pi = config["procurement_intent"]
        self.intent = {
            "baseline": pi.get("baseline", 0),
            "points": pi["points"],
            "max": pi["max"],
            "strong": [_phrase(p) for p in pi.get("strong") or []],
            "medium": [_phrase(p) for p in pi.get("medium") or []],
            "weak": [_phrase(p) for p in pi.get("weak") or []],
        }
        self.deployment = {
            key: {
                "label": spec.get("label", key),
                "patterns": [compile_bounded(p) for p in spec.get("patterns") or []],
            }
            for key, spec in config["deployment"].items()
        }
        dr = config.get("deployment_review") or {}
        self.deployment_review = {
            "patterns": [compile_bounded(p) for p in dr.get("patterns") or []],
            "reason": dr.get("reason", "Hosting model needs review"),
        }
        self.codes = {
            scheme.upper(): {str(code): desc for code, desc in (mapping or {}).items()}
            for scheme, mapping in (config.get("classification_codes") or {}).items()
        }
        self.buyer_patterns = [
            re.compile(p) for p in (config.get("buyer_context") or {}).get("patterns") or []
        ]
        self.acronyms = config.get("ambiguous_acronyms") or {}
        self.false_positives = {
            name: {
                "reason": spec["reason"],
                "cap_key": spec["cap_key"],
                "software_override": bool(spec.get("software_override")),
                "patterns": [compile_bounded(p) for p in spec.get("patterns") or []],
            }
            for name, spec in (config.get("false_positives") or {}).items()
        }
        self.software_override_phrases = [_phrase(p) for p in config.get("software_override_phrases") or []]
        self.inactive_statuses = {s.lower() for s in config.get("inactive_statuses") or []}

    # -- helpers ------------------------------------------------------------
    @staticmethod
    def _contains(text: str, phrase: str) -> bool:
        return f" {phrase} " in text

    def _tier_hits(self, spec: dict, title: str, body: str) -> list[Hit]:
        hits: list[Hit] = []
        for tier in ("strong", "medium", "weak"):
            for phrase in spec.get(tier) or []:
                in_title = self._contains(title, phrase)
                if in_title or self._contains(body, phrase):
                    hits.append(Hit(phrase, tier, in_title))
        return hits

    def _acronym_gate(self, text: str) -> AcronymGate:
        """Decide whether ambiguous acronyms ('SDS', 'REACH', ...) really apply.

        A phrase containing a blocked token is dropped from the topic score:
        "SDS management" must not score when the notice means software-defined
        storage, and "REACH" must not score when it is the English verb.
        """
        gate = AcronymGate()
        for token, spec in self.acronyms.items():
            token_n = _phrase(token)
            positions = [m.start() for m in re.finditer(rf" {re.escape(token_n)} ", text)]
            unrelated = None
            for pattern in spec.get("unrelated_patterns") or []:
                if compile_bounded(pattern).search(text):
                    unrelated = spec.get("unrelated_reason", f"Unrelated use of the acronym '{token}'")
                    break
            if unrelated:
                gate.blocked.add(token_n)
                gate.disqualifiers.append(unrelated)
                if spec.get("cap_key"):
                    gate.cap_keys.append(str(spec["cap_key"]))
                continue
            if not positions:
                continue
            window = int(spec.get("window", 200))
            terms = [_phrase(t) for t in spec.get("context_terms") or []]
            context_ok = any(
                any(f" {t} " in text[max(0, pos - window) : pos + window + 5] for t in terms)
                for pos in positions
            )
            if context_ok:
                continue
            gate.blocked.add(token_n)
            if spec.get("flag_when_missing_context") and spec.get("missing_context_flag"):
                gate.review_flags.append(str(spec["missing_context_flag"]))
        return gate

    def _classify_deployment(self, text: str) -> tuple[str, str | None, str | None]:
        """Returns (deployment_fit, label, matched snippet)."""
        for key in _DEPLOYMENT_ORDER:
            spec = self.deployment.get(key)
            if not spec:
                continue
            for pattern in spec["patterns"]:
                m = pattern.search(text)
                if m:
                    return key, spec["label"], m.group(0).strip()[:160]
        return DEP_UNSPECIFIED, None, None

    def _code_hits(self, codes: Iterable[Any]) -> list[tuple[str, str, str]]:
        """Prefix-match tender codes against configured signal codes."""
        out: list[tuple[str, str, str]] = []
        seen: set[tuple[str, str]] = set()
        for entry in codes or []:
            if isinstance(entry, dict):
                scheme = str(entry.get("scheme") or "").upper()
                code = str(entry.get("code") or "")
            else:
                raw = str(entry)
                scheme, _, code = raw.partition(":") if ":" in raw else ("", "", raw)
                scheme = scheme.upper()
            code = re.sub(r"[^0-9A-Za-z]", "", code)
            if not code:
                continue
            schemes = [scheme] if scheme in self.codes else list(self.codes)
            for sch in schemes:
                for cfg_code, desc in self.codes[sch].items():
                    prefix = cfg_code.rstrip("0") or cfg_code
                    if code.startswith(prefix) and (sch, cfg_code) not in seen:
                        seen.add((sch, cfg_code))
                        out.append((sch, cfg_code, desc))
        return out

    # -- scoring ------------------------------------------------------------
    def score(
        self,
        *,
        title: str | None,
        description: str | None,
        buyer_name: str | None = None,
        classification_codes: Iterable[Any] | None = None,
        deadline: datetime | None = None,
        status: str | None = None,
        notice_type: str | None = None,
        now: datetime | None = None,
    ) -> RelevanceResult:
        now = now or datetime.now(UTC).replace(tzinfo=None)
        title_t = normalize(title)
        body_t = normalize(" ".join(x for x in (title, description, notice_type) if x))
        buyer_t = normalize(buyer_name)
        full_t = normalize(" ".join(x for x in (title, description, buyer_name, notice_type) if x))

        reasons: list[str] = []
        disqualifiers: list[str] = []
        review_flags: list[str] = []
        manual_review = False

        # --- ambiguous acronyms (SDS / REACH) ------------------------------
        gate = self._acronym_gate(full_t)
        review_flags.extend(gate.review_flags)
        disqualifiers.extend(gate.disqualifiers)
        if gate.review_flags:
            manual_review = True

        # --- topic: per-profile phrase matching ----------------------------
        profile_scores: dict[str, int] = {}
        profile_hits: dict[str, list[Hit]] = {}
        for name, spec in self.profiles.items():
            hits = self._tier_hits(spec, title_t, body_t)
            if gate.blocked:
                hits = [h for h in hits if not gate.blocked.intersection(h.phrase.split())]
            if not hits:
                continue
            points = 0.0
            for hit in hits:
                base = self.phrase_points[hit.tier]
                points += base * (self.weights["title_multiplier"] if hit.in_title else 1.0)
            concepts = min(
                len(hits) * self.weights["distinct_concept_points"],
                self.weights["distinct_concept_max"],
            )
            profile_scores[name] = int(min(100, points + concepts))
            profile_hits[name] = hits

        code_hits = self._code_hits(classification_codes or [])
        code_points = min(
            len(code_hits) * self.weights["classification_code_points"],
            self.weights["classification_code_max"],
        )

        ranked = sorted(profile_scores.items(), key=lambda kv: kv[1], reverse=True)
        best_name, best_score = ranked[0] if ranked else (None, 0)
        second = ranked[1][1] if len(ranked) > 1 else 0
        modules = [n for n, s in ranked if s >= self.weights["module_threshold"]]
        module_bonus = min(
            max(0, len(modules) - 1) * self.weights["module_bonus"],
            self.weights["module_bonus_max"],
        )
        buyer_bonus = 0
        if any(p.search(buyer_t) for p in self.buyer_patterns) and best_score > 0:
            buyer_bonus = self.weights["buyer_context_points"]

        topic = 0
        if best_score:
            topic = int(
                min(
                    100,
                    best_score
                    + second * self.weights["secondary_profile_ratio"]
                    + module_bonus
                    + buyer_bonus
                    + code_points,
                )
            )
        elif code_points:
            topic = int(min(30, code_points))

        # --- product / deployment fit --------------------------------------
        fit_hits = self._tier_hits(self.product_fit, title_t, body_t)
        fit_points = min(
            sum(self.product_fit["points"][h.tier] for h in fit_hits),
            self.product_fit["max"],
        )
        deployment_fit, deployment_label, deployment_snippet = self._classify_deployment(full_t)
        product_fit = max(0, min(100, int(fit_points + _DEPLOYMENT_BONUS[deployment_fit])))
        if deployment_fit in _NON_FIT_DEPLOYMENTS:
            product_fit = 0

        for pattern in self.deployment_review["patterns"]:
            m = pattern.search(full_t)
            if m:
                review_flags.append(f"{self.deployment_review['reason']}: '{m.group(0).strip()[:120]}'")
                manual_review = True
                break

        # --- procurement intent -------------------------------------------
        intent_hits = self._tier_hits(self.intent, title_t, body_t)
        intent = min(
            self.intent["baseline"] + sum(self.intent["points"][h.tier] for h in intent_hits),
            self.intent["max"],
        )

        # --- combine -------------------------------------------------------
        final = (
            topic * self.weights["topic"]
            + product_fit * self.weights["product_fit"]
            + intent * self.weights["procurement_intent"]
        )

        # --- caps ----------------------------------------------------------
        has_software_proof = any(self._contains(body_t, p) for p in self.software_override_phrases)
        applicable_caps: list[int] = []
        if deployment_fit == DEP_ON_PREM:
            applicable_caps.append(self.caps["mandatory_on_premises"])
            disqualifiers.append(
                f"Mandatory on-premises deployment: '{deployment_snippet}'"
                if deployment_snippet
                else "Mandatory on-premises deployment"
            )
        if deployment_fit == DEP_OFFLINE:
            applicable_caps.append(self.caps["offline_or_air_gapped"])
            disqualifiers.append(
                f"Offline / air-gapped operation required: '{deployment_snippet}'"
                if deployment_snippet
                else "Offline / air-gapped operation required"
            )
        for cap_key in gate.cap_keys:
            applicable_caps.append(self.caps[cap_key])

        for _name, spec in self.false_positives.items():
            match = next((p.search(full_t) for p in spec["patterns"] if p.search(full_t)), None)
            if not match:
                continue
            if spec["software_override"] and has_software_proof:
                review_flags.append(
                    f"{spec['reason']} - but software-platform language is also present; confirm the scope"
                )
                manual_review = True
                continue
            applicable_caps.append(self.caps[spec["cap_key"]])
            disqualifiers.append(f"{spec['reason']}: '{match.group(0).strip()[:140]}'")

        if applicable_caps:
            final = min(final, min(applicable_caps))

        # --- actionability -------------------------------------------------
        is_actionable = True
        status_l = (status or "").strip().lower()
        if status_l in self.inactive_statuses:
            is_actionable = False
            final *= self.weights["cancelled_multiplier"]
            review_flags.append(f"Notice status '{status}' - not actionable")
        if deadline is not None:
            dl = deadline.replace(tzinfo=None) if deadline.tzinfo else deadline
            if dl < now:
                is_actionable = False
                final *= self.weights["expired_multiplier"]
                review_flags.append(f"Submission deadline passed ({dl.date().isoformat()}) - not actionable")

        score = max(0, min(100, int(round(final))))

        # --- reasons -------------------------------------------------------
        if best_name:
            label = self.profiles[best_name]["label"]
            title_hits = [h.phrase for h in profile_hits[best_name] if h.in_title]
            desc_hits = [h.phrase for h in profile_hits[best_name] if not h.in_title]
            if title_hits:
                reasons.append(f"Title matches {label}: {_quote(title_hits[:3])}")
            if desc_hits:
                reasons.append(f"Description mentions {label} concepts: {_quote(desc_hits[:4])}")
            if len(modules) > 1:
                other = ", ".join(self.profiles[n]["label"] for n in modules[:4])
                reasons.append(f"Requests {len(modules)} supported capability areas: {other}")
        if deployment_label and deployment_fit not in _NON_FIT_DEPLOYMENTS:
            reasons.append(
                f"{deployment_label}" + (f": '{deployment_snippet}'" if deployment_snippet else "")
            )
        elif deployment_fit == DEP_UNSPECIFIED and best_score:
            reasons.append("Deployment model not specified - cloud delivery is not excluded")
        strong_fit = [h.phrase for h in fit_hits if h.tier in ("strong", "medium")]
        if strong_fit:
            reasons.append(
                f"Software/hosting requirements match a cloud SaaS delivery model: {_quote(strong_fit[:4])}"
            )
        strong_intent = [h.phrase for h in intent_hits if h.tier in ("strong", "medium")]
        if strong_intent:
            reasons.append(f"Procurement language indicates a software purchase: {_quote(strong_intent[:3])}")
        for scheme, code, desc in code_hits[:3]:
            reasons.append(f"{scheme} code {code} ({desc}) is a relevant classification signal")
        if buyer_bonus:
            reasons.append("Buyer profile is consistent with chemical / EHS / IT procurement")
        if not reasons:
            reasons.append("No SDS, chemical-compliance or EHS software signals found")

        # --- fit status ----------------------------------------------------
        if deployment_fit in _NON_FIT_DEPLOYMENTS or (
            gate.disqualifiers and score < self.bands["possible_fit"]
        ):
            fit_status = FIT_NOT
        elif score >= self.bands["excellent_fit"]:
            fit_status = FIT_HIGH
        elif score >= self.bands["good_fit"]:
            fit_status = FIT_GOOD
        elif score >= self.bands["possible_fit"]:
            fit_status = FIT_POSSIBLE
        else:
            fit_status = FIT_NOT
        if deployment_fit == DEP_HYBRID and fit_status != FIT_NOT:
            manual_review = True
            review_flags.append(
                "Both cloud and on-premises deployment are permitted - confirm a cloud-only proposal is acceptable"
            )
        if manual_review and score >= self.bands["manual_review_min"] and fit_status != FIT_NOT:
            fit_status = FIT_REVIEW

        return RelevanceResult(
            relevance_score=score,
            relevance_category=best_name,
            relevance_category_label=self.profiles[best_name]["label"] if best_name else None,
            fit_status=fit_status,
            deployment_fit=deployment_fit,
            relevance_reasons=_dedupe(reasons),
            disqualifiers=_dedupe(disqualifiers),
            review_flags=_dedupe(review_flags),
            topic_relevance_score=topic,
            product_fit_score=product_fit,
            procurement_intent_score=int(intent),
            is_actionable=is_actionable,
            profile_scores=profile_scores,
        )

    def score_obj(self, obj: Any, now: datetime | None = None) -> RelevanceResult:
        """Score anything exposing the normalized tender attributes."""
        return self.score(
            title=getattr(obj, "title", None),
            description=getattr(obj, "description", None),
            buyer_name=getattr(obj, "buyer_name", None),
            classification_codes=getattr(obj, "classification_codes", None) or [],
            deadline=getattr(obj, "deadline", None),
            status=getattr(obj, "status", None),
            notice_type=getattr(obj, "notice_type", None),
            now=now,
        )

    # -- metadata for the API ----------------------------------------------
    def profile_metadata(self) -> list[dict[str, str]]:
        return [{"key": k, "label": v["label"]} for k, v in self.profiles.items()]

    def band_for(self, score: int) -> str:
        if score >= self.bands["excellent_fit"]:
            return "excellent"
        if score >= self.bands["good_fit"]:
            return "good"
        if score >= self.bands["possible_fit"]:
            return "possible"
        if score >= self.bands["weak_fit"]:
            return "weak"
        return "irrelevant"


def _quote(items: list[str]) -> str:
    return ", ".join(f"'{i}'" for i in items)


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    cfg_path = Path(path or get_settings().relevance_config_path)
    with cfg_path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


@lru_cache(maxsize=4)
def get_engine(path: str | None = None) -> RelevanceEngine:
    return RelevanceEngine(load_config(path))
