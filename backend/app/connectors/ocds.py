"""Shared normalization for OCDS release packages (UK FTS + Contracts Finder)."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from app.connectors.base import (
    STAGE_AWARD,
    STAGE_PLANNING,
    STAGE_TENDER,
    NormalizedTender,
    parse_datetime,
    status_from_deadline,
)

_TAG_STAGE = {
    "planning": STAGE_PLANNING,
    "planningupdate": STAGE_PLANNING,
    "tender": STAGE_TENDER,
    "tenderamendment": STAGE_TENDER,
    "tenderupdate": STAGE_TENDER,
    "tendercancellation": STAGE_TENDER,
    "award": STAGE_AWARD,
    "awardupdate": STAGE_AWARD,
    "contract": STAGE_AWARD,
    "contractamendment": STAGE_AWARD,
    "implementation": STAGE_AWARD,
    "implementationupdate": STAGE_AWARD,
}


def stage_from_tags(tags: Iterable[str]) -> str:
    stages = {_TAG_STAGE.get(str(t).lower().replace(" ", ""), STAGE_TENDER) for t in tags or []}
    for stage in (STAGE_TENDER, STAGE_PLANNING, STAGE_AWARD):
        if stage in stages:
            return stage
    return STAGE_TENDER


def _classifications(tender: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    def add(node: Any) -> None:
        if not isinstance(node, dict):
            return
        code = node.get("id")
        if code in (None, ""):
            return
        scheme = str(node.get("scheme") or "CPV").upper()
        key = (scheme, str(code))
        if key in seen:
            return
        seen.add(key)
        out.append({"scheme": scheme, "code": str(code), "description": node.get("description")})

    add(tender.get("classification"))
    for item in tender.get("items") or []:
        if isinstance(item, dict):
            add(item.get("classification"))
            for extra in item.get("additionalClassifications") or []:
                add(extra)
    for extra in tender.get("additionalClassifications") or []:
        add(extra)
    return out


def _documents(*nodes: Any) -> list[str]:
    urls: list[str] = []
    for node in nodes:
        for doc in (node or {}).get("documents") or []:
            url = (doc or {}).get("url")
            if url and url not in urls:
                urls.append(str(url))
    return urls[:25]


def _buyer(release: dict[str, Any]) -> tuple[str | None, str | None]:
    buyer = release.get("buyer") or {}
    name = buyer.get("name")
    country = None
    buyer_id = buyer.get("id")
    for party in release.get("parties") or []:
        if not isinstance(party, dict):
            continue
        roles = [str(r).lower() for r in party.get("roles") or []]
        if (buyer_id and party.get("id") == buyer_id) or "buyer" in roles:
            name = name or party.get("name")
            country = ((party.get("address") or {}).get("country")) or country
            break
    return name, (str(country).upper() if country else None)


def _delivery_location(tender: dict[str, Any]) -> str | None:
    for item in tender.get("items") or []:
        place = (item or {}).get("deliveryAddress") or {}
        parts = [place.get("locality"), place.get("region"), place.get("countryName")]
        text = ", ".join(p for p in parts if p)
        if text:
            return text[:500]
    for area in tender.get("deliveryAddresses") or []:
        parts = [area.get("locality"), area.get("region"), area.get("countryName")]
        text = ", ".join(p for p in parts if p)
        if text:
            return text[:500]
    return None


def normalize_release(
    release: dict[str, Any],
    *,
    source: str,
    notice_url_template: str | None = None,
) -> NormalizedTender | None:
    """Normalize a single OCDS release. Returns None when unusable."""
    notice_id = release.get("id") or release.get("ocid")
    if not notice_id:
        return None
    tender = release.get("tender") or {}
    planning = release.get("planning") or {}
    awards = release.get("awards") or []
    stage = stage_from_tags(release.get("tag") or [])

    title = tender.get("title") or (planning.get("project") or {}).get("title") or ""
    description = tender.get("description") or (planning.get("project") or {}).get("description")
    if not title and description:
        title = str(description)[:200]

    tender_period = tender.get("tenderPeriod") or {}
    deadline, deadline_tz = parse_datetime(tender_period.get("endDate"))
    published, published_tz = parse_datetime(tender.get("datePublished") or release.get("date"))
    updated, _ = parse_datetime(release.get("date"))

    value_node = tender.get("value") or tender.get("minValue") or {}
    amount = value_node.get("amount")
    try:
        amount = float(amount) if amount not in (None, "") else None
    except (TypeError, ValueError):
        amount = None

    buyer_name, buyer_country = _buyer(release)
    documents = _documents(tender, planning, *(a for a in awards if isinstance(a, dict)))
    source_url = None
    for doc in (tender.get("documents") or []) + (planning.get("documents") or []):
        if isinstance(doc, dict) and doc.get("url") and "notice" in str(doc.get("documentType", "")).lower():
            source_url = str(doc["url"])
            break
    if not source_url and documents:
        source_url = documents[0]
    if not source_url and notice_url_template:
        source_url = notice_url_template.format(notice_id)

    status = tender.get("status") or (STAGE_AWARD if awards else None)
    return NormalizedTender(
        source=source,
        source_notice_id=str(notice_id),
        source_url=source_url,
        reference_number=str(release.get("ocid") or tender.get("id") or notice_id),
        title=str(title)[:1000] or str(notice_id),
        description=str(description) if description else None,
        buyer_name=buyer_name,
        buyer_country=buyer_country or "GB",
        delivery_location=_delivery_location(tender),
        publication_date=published,
        deadline=deadline,
        source_updated_at=updated,
        source_timezone=deadline_tz or published_tz,
        status=str(status) if status else status_from_deadline(deadline),
        procurement_stage=stage,
        notice_type=_notice_type(release, tender, planning),
        estimated_value=amount,
        currency=value_node.get("currency"),
        classification_codes=_classifications(tender),
        document_urls=documents,
        language=release.get("language") or "en",
        raw_payload=release,
    )


def _notice_type(release: dict[str, Any], tender: dict[str, Any], planning: dict[str, Any]) -> str | None:
    for node in (tender, planning):
        for doc in (node or {}).get("documents") or []:
            if isinstance(doc, dict) and doc.get("noticeType"):
                return str(doc["noticeType"])
            if isinstance(doc, dict) and doc.get("documentType"):
                return str(doc["documentType"])
    tags = release.get("tag") or []
    return str(tags[0]) if tags else None
