"""UK Find a Tender Service (FTS) - OCDS release packages.

GET https://www.find-tender.service.gov.uk/api/1.0/ocdsReleasePackages
Cursor pagination via ``links.next``. Planning, tender and award stages are all
captured; tender-stage releases are the primary opportunities.
"""

from __future__ import annotations

from datetime import datetime

from app.connectors.base import STAGE_TENDER, NormalizedTender, TenderConnector
from app.connectors.ocds import normalize_release

API_URL = "https://www.find-tender.service.gov.uk/api/1.0/ocdsReleasePackages"
NOTICE_URL = "https://www.find-tender.service.gov.uk/Notice/{}"


class FindATenderConnector(TenderConnector):
    source_name = "find_a_tender"
    display_name = "UK Find a Tender"
    homepage = "https://www.find-tender.service.gov.uk"
    prefilter = True
    notes = (
        "OCDS releases by updatedFrom/updatedTo with cursor pagination. Tender-stage releases are primary."
    )

    async def fetch(self, date_from: datetime, date_to: datetime) -> list[NormalizedTender]:
        date_from, date_to = self.clamp_window(date_from, date_to)
        params = {
            "updatedFrom": date_from.strftime("%Y-%m-%dT%H:%M:%S"),
            "updatedTo": date_to.strftime("%Y-%m-%dT%H:%M:%S"),
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

    @staticmethod
    def primary_stage() -> str:
        return STAGE_TENDER
