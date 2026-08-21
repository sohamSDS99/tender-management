"""UK Contracts Finder - OCDS search endpoint.

GET https://www.contractsfinder.service.gov.uk/Published/Notices/OCDS/Search
Filtered by publication date + stage, cursor pagination via ``links.next``.
"""

from __future__ import annotations

from datetime import datetime

from app.connectors.base import NormalizedTender, TenderConnector
from app.connectors.ocds import normalize_release

API_URL = "https://www.contractsfinder.service.gov.uk/Published/Notices/OCDS/Search"
NOTICE_URL = "https://www.contractsfinder.service.gov.uk/Notice/{}"
STAGES = "tender,planning"


class ContractsFinderConnector(TenderConnector):
    source_name = "contracts_finder"
    display_name = "UK Contracts Finder"
    homepage = "https://www.contractsfinder.service.gov.uk"
    prefilter = True
    notes = "OCDS search filtered by publishedFrom/publishedTo and stages=tender,planning; cursor pagination."

    async def fetch(self, date_from: datetime, date_to: datetime) -> list[NormalizedTender]:
        date_from, date_to = self.clamp_window(date_from, date_to)
        params = {
            "publishedFrom": date_from.strftime("%Y-%m-%dT%H:%M:%S"),
            "publishedTo": date_to.strftime("%Y-%m-%dT%H:%M:%S"),
            "stages": STAGES,
            "limit": min(self.settings.page_size, 100),
        }
        url: str | None = API_URL
        out: list[NormalizedTender] = []
        seen: set[str] = set()
        async with self.client() as client:
            for page in range(self.settings.max_pages_per_source):
                data = await self.request(
                    client, "GET", url, params=params if page == 0 else None, expect="json"
                )
                releases = data.get("releases") or []
                self.log_progress(page=page + 1, received=len(releases))
                for raw in releases:
                    try:
                        tender = normalize_release(
                            raw, source=self.source_name, notice_url_template=NOTICE_URL
                        )
                    except Exception:
                        continue
                    if not tender or tender.source_notice_id in seen:
                        continue
                    if not self.keep(tender.title, tender.description, tender.buyer_name):
                        continue
                    seen.add(tender.source_notice_id)
                    out.append(tender)
                url = ((data.get("links") or {}).get("next")) or None
                if not url or not releases:
                    break
        return out
