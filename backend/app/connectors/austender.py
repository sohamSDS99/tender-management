"""AusTender current ATM (approach to market) RSS feed.

https://www.tenders.gov.au/public_data/rss/rss.xml
The feed carries title, link, description and publication date only; the
AusTender link is preserved as the authoritative source.
"""

from __future__ import annotations

import email.utils
import re
from datetime import datetime
from xml.etree import ElementTree

from app.connectors.base import (
    STAGE_TENDER,
    ConnectorError,
    NormalizedTender,
    TenderConnector,
    parse_datetime,
    to_utc_naive,
)

FEED_URL = "https://www.tenders.gov.au/public_data/rss/rss.xml"
_TAGS = re.compile(r"<[^>]+>")
_ATM_ID = re.compile(r"^([A-Za-z0-9][\w./-]{2,40}):\s*")
_UNSAFE_XML = re.compile(r"<!(DOCTYPE|ENTITY)", re.IGNORECASE)


class AusTenderConnector(TenderConnector):
    source_name = "austender"
    display_name = "AusTender"
    homepage = "https://www.tenders.gov.au"
    prefilter = True
    notes = (
        "Current ATM RSS feed. Only title, link, description and publication date are published, "
        "so closing dates and values are not available from this feed."
    )

    async def fetch(self, date_from: datetime, date_to: datetime) -> list[NormalizedTender]:
        async with self.client() as client:
            text = await self.request(client, "GET", FEED_URL, expect="xml")
        if _UNSAFE_XML.search(text[:4000]):
            raise ConnectorError(self.source_name, "refusing XML containing a DOCTYPE/ENTITY declaration")
        try:
            root = ElementTree.fromstring(text)
        except ElementTree.ParseError as exc:
            raise ConnectorError(self.source_name, f"invalid RSS/XML: {exc}") from exc
        out: list[NormalizedTender] = []
        seen: set[str] = set()
        for item in root.iter("item"):
            try:
                tender = self._normalize(item)
            except Exception:
                continue
            if not tender or tender.source_notice_id in seen:
                continue
            if not self.keep(tender.title, tender.description):
                continue
            seen.add(tender.source_notice_id)
            out.append(tender)
        self.log_progress(items=len(list(root.iter("item"))), kept=len(out))
        return out

    def _normalize(self, item: ElementTree.Element) -> NormalizedTender | None:
        def text(tag: str) -> str | None:
            node = item.find(tag)
            return (node.text or "").strip() if node is not None and node.text else None

        link = text("link") or text("guid")
        title = text("title")
        if not link or not title:
            return None
        notice_id = link.rstrip("/").rsplit("/", 1)[-1]
        published, tz_label = _parse_rss_date(text("pubDate"))
        reference = None
        match = _ATM_ID.match(title)
        if match:
            reference = match.group(1)
        description = _TAGS.sub(" ", text("description") or "").replace("&amp;", "&").strip() or None
        return NormalizedTender(
            source=self.source_name,
            source_notice_id=notice_id,
            source_url=link,
            reference_number=reference,
            title=title[:1000],
            description=description,
            buyer_name=None,
            buyer_country="AU",
            delivery_location=None,
            publication_date=published,
            deadline=None,
            source_updated_at=published,
            source_timezone=tz_label,
            status="open",
            procurement_stage=STAGE_TENDER,
            notice_type="Approach to market",
            estimated_value=None,
            currency="AUD",
            classification_codes=[],
            document_urls=[link],
            language="en",
            raw_payload={
                "title": title,
                "link": link,
                "description": text("description"),
                "pubDate": text("pubDate"),
                "guid": text("guid"),
            },
        )


def _parse_rss_date(value: str | None) -> tuple[datetime | None, str | None]:
    if not value:
        return None, None
    try:
        parsed = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return parse_datetime(value)
    if parsed is None:
        return None, None
    label = parsed.tzname() or (value.strip().rsplit(" ", 1)[-1] if " " in value else None)
    return to_utc_naive(parsed), label
