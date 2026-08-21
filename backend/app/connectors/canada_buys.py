"""CanadaBuys open data CSV feeds.

Machine-readable scheduled feeds (no HTML scraping):
  new  notices: newTenderNotice-nouvelAvisAppelOffres.csv   (frequent updates)
  open notices: openTenderNotice-ouvertAvisAppelOffres.csv  (reconciliation)
Bilingual English/French columns are both preserved.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Iterator
from datetime import datetime
from typing import Any

from app.connectors.base import (
    NormalizedTender,
    TenderConnector,
    parse_datetime,
    stage_from_code,
    status_from_deadline,
)

NEW_FEED = "https://canadabuys.canada.ca/opendata/pub/newTenderNotice-nouvelAvisAppelOffres.csv"
OPEN_FEED = "https://canadabuys.canada.ca/opendata/pub/openTenderNotice-ouvertAvisAppelOffres.csv"
NOTICE_URL = "https://canadabuys.canada.ca/en/tender-opportunities/tender-notice/{}"


def _get(row: dict[str, str], *names: str) -> str | None:
    for name in names:
        value = (row.get(name) or "").strip()
        if value:
            return value
    return None


class CanadaBuysConnector(TenderConnector):
    source_name = "canada_buys"
    display_name = "CanadaBuys"
    homepage = "https://canadabuys.canada.ca"
    prefilter = True
    notes = (
        "CSV open-data feeds. The new-notices feed carries frequent updates, the open-notices "
        "feed is used for reconciliation (disable with ENABLE_CANADA_BUYS_OPEN_FEED=false). "
        "Bilingual EN/FR fields are both stored."
    )

    async def fetch(self, date_from: datetime, date_to: datetime) -> list[NormalizedTender]:
        date_from, date_to = self.clamp_window(date_from, date_to)
        feeds = [("new", NEW_FEED)]
        if self.settings.enable_canada_buys_open_feed:
            feeds.append(("open", OPEN_FEED))
        out: list[NormalizedTender] = []
        seen: set[str] = set()
        async with self.client() as client:
            for feed_name, url in feeds:
                text = await self.request(client, "GET", url, expect="csv")
                kept = 0
                for row in _rows(text):
                    try:
                        tender = self._normalize(row, feed_name)
                    except Exception:
                        continue
                    if not tender or tender.source_notice_id in seen:
                        continue
                    if not self.keep(tender.title, tender.description, tender.buyer_name):
                        continue
                    seen.add(tender.source_notice_id)
                    out.append(tender)
                    kept += 1
                self.log_progress(feed=feed_name, kept=kept)
        return out

    def _normalize(self, row: dict[str, str], feed_name: str) -> NormalizedTender | None:
        reference = _get(row, "referenceNumber-numeroReference")
        if not reference:
            return None
        title_en = _get(row, "title-titre-eng")
        title_fr = _get(row, "title-titre-fra")
        desc_en = _get(row, "tenderDescription-descriptionAppelOffres-eng")
        desc_fr = _get(row, "tenderDescription-descriptionAppelOffres-fra")
        description = "\n\n".join(p for p in (desc_en, desc_fr) if p) or None
        published, published_tz = parse_datetime(_get(row, "publicationDate-datePublication"), ("%Y-%m-%d",))
        deadline, deadline_tz = parse_datetime(
            _get(row, "tenderClosingDate-appelOffresDateCloture"), ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d")
        )
        amended, _ = parse_datetime(_get(row, "amendmentDate-dateModification"))
        status = _get(row, "tenderStatus-appelOffresStatut-eng") or status_from_deadline(deadline)
        notice_type = _get(row, "noticeType-avisType-eng")
        codes: list[dict[str, Any]] = []
        for scheme, column in (("UNSPSC", "unspsc"), ("GSIN", "gsin-nibs")):
            raw_codes = _get(row, column) or ""
            for code in raw_codes.replace("\n", "*").split("*"):
                code = code.strip()
                if code:
                    codes.append({"scheme": scheme, "code": code})
        documents = [
            u.strip()
            for u in (_get(row, "attachment-piecesJointes-eng") or "").replace("\n", "*").split("*")
            if u.strip().startswith("http")
        ]
        return NormalizedTender(
            source=self.source_name,
            source_notice_id=reference,
            source_url=_get(row, "noticeURL-URLavis-eng", "noticeURL-URLavis-fra")
            or NOTICE_URL.format(reference),
            reference_number=_get(row, "solicitationNumber-numeroSollicitation") or reference,
            title=(title_en or title_fr or reference)[:1000],
            description=description,
            buyer_name=_get(
                row,
                "contractingEntityName-nomEntitContractante-eng",
                "contractingEntityName-nomEntitContractante-fra",
            ),
            buyer_country="CA",
            delivery_location=_get(
                row, "regionsOfDelivery-regionsLivraison-eng", "regionsOfDelivery-regionsLivraison-fra"
            ),
            publication_date=published,
            deadline=deadline,
            source_updated_at=amended or published,
            source_timezone=deadline_tz or published_tz,
            status=status,
            procurement_stage=stage_from_code(notice_type),
            notice_type=notice_type,
            estimated_value=None,
            currency="CAD",
            classification_codes=codes[:25],
            document_urls=documents[:25],
            language="en" if title_en else "fr",
            raw_payload={"feed": feed_name, **{k: v for k, v in row.items() if v}},
        )


def _rows(text: str) -> Iterator[dict[str, str]]:
    reader = csv.DictReader(io.StringIO(text))
    for row in reader:
        if row:
            yield row
