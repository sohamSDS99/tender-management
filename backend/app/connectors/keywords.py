"""Topical search phrases used to query high-volume sources.

These are *fetch* filters (keep the ingested volume sane), not relevance rules:
scoring always happens afterwards in app.services.relevance.
"""

from __future__ import annotations

import re
import unicodedata

# Ordered roughly by precision. Used for TED full-text queries, SAM.gov `q`,
# World Bank `qterm`, and as a client-side prefilter for feed-style sources.
SEARCH_PHRASES: tuple[str, ...] = (
    "safety data sheet",
    "safety data sheets",
    "material safety data sheet",
    "SDS management",
    "SDS authoring",
    "chemical inventory",
    "chemical management",
    "chemical compliance",
    "hazard communication",
    "hazardous substance register",
    "GHS",
    "REACH compliance",
    "EHS software",
    "EHS management system",
    "HSE software",
    "QHSE",
    "occupational health and safety management system",
    "health and safety management system",
    "incident management system",
    "incident reporting software",
    "inspection management",
    "audit management software",
    "environmental management system software",
    "Sicherheitsdatenblatt",
    "Gefahrstoffmanagement",
    "fiche de donnees de securite",
    "logiciel HSE",
    "ficha de datos de seguridad",
    "ficha de dados de seguranca",
    "gestao de produtos quimicos",
)

# Short, cheap prefilter tokens for CSV/RSS/bulk feeds where no server-side
# search exists. Deliberately broader than SEARCH_PHRASES: the relevance
# engine does the precise work, this only avoids storing the whole feed.
PREFILTER_TERMS: tuple[str, ...] = (
    "safety data sheet",
    "sicherheitsdatenblatt",
    "fiche de donnees de securite",
    "ficha de datos de seguridad",
    "ficha de dados de seguranca",
    "msds",
    "sds",
    "ghs",
    "reach",
    "whmis",
    "chemical",
    "chemicals",
    "chimique",
    "quimic",
    "hazardous",
    "hazard",
    "ehs",
    "hse",
    "qhse",
    "occupational health",
    "occupational safety",
    "health and safety",
    "safety management",
    "incident management",
    "incident reporting",
    "inspection management",
    "audit management",
    "environmental management",
    "environmental compliance",
    "compliance management",
    "risk assessment",
    "seguranca",
    "securite",
    "seguridad",
    "arbeitsschutz",
    "gefahrstoff",
)

_NON_ALNUM = re.compile(r"[^0-9a-z]+")


def _fold(text: str) -> str:
    folded = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return " " + _NON_ALNUM.sub(" ", folded.lower()).strip() + " "


_PREFILTER_FOLDED = tuple(_fold(t).strip() for t in PREFILTER_TERMS)


def looks_relevant(*texts: str | None) -> bool:
    """Cheap prefilter for feeds that cannot be searched server-side."""
    blob = _fold(" ".join(t for t in texts if t))
    return any(f" {term} " in blob for term in _PREFILTER_FOLDED)
