"""Germany - Datenservice Öffentlicher Einkauf (Bekanntmachungsservice).

GET https://oeffentlichevergabe.de/api/notice-exports?pubDay=YYYY-MM-DD&format=ocds.zip
Docs: https://oeffentlichevergabe.de/  ·  Licence: CC0 (open data, no authentication)

This is the federal aggregator over 30+ German procurement platforms (evergabe.de,
DTVP, subreport, the Länder marketplaces), and it is the only source in this system
that carries **below-EU-threshold** German notices. TED holds above-threshold notices
plus a thin voluntary tail (Germany filed 128 voluntary notices out of 112,661 in
2026), so a UVgO award like a research institute's chemical-management software never
reaches TED at all.

Three shapes of German national notice force work that `ocds.normalize_release`
cannot do on its own, which is why this connector post-processes every release:

* **The deadline is prose, not a field.** UVgO notices carry no `tenderPeriod`; the
  submission date lives inside the description as "i) Angebotsfrist: 10.08.2026,
  10:00 Uhr". Without `_deadline_from_text` every German notice would land with
  `deadline=None` and be scored as though it had no closing date.
* **There is usually no CPV.** Relevance therefore rests on the title and the
  description, so the German vocabulary in `relevance_profiles.yaml` is load-bearing
  for this source in a way it is not for TED.
* **The shared OCDS normaliser defaults `buyer_country` to "GB"** (it was written for
  the two UK feeds). We resolve the real country from the delivery address.

The export is served only as a ZIP - the API rejects every uncompressed
representation with HTTP 406 - so this connector reads bytes from `self.client()`
rather than `self.request()`, which handles json/text/xml/csv only. The retry,
size-guard and progress-logging semantics of `base.request` are mirrored here.
"""

from __future__ import annotations

import io
import json
import re
import zipfile
from datetime import datetime, timedelta
from typing import Any

import httpx

from app.connectors.base import (
    ConnectorError,
    NormalizedTender,
    TenderConnector,
)
from app.connectors.ocds import normalize_release

API_URL = "https://oeffentlichevergabe.de/api/notice-exports"
EXPORT_FORMAT = "ocds.zip"
_ZIP_MAGIC = b"PK\x03\x04"

# "Angebotsfrist: 10.08.2026, 10:00 Uhr" - also covers Teilnahmeantrags-/Bewerbungsfrist
# and the eForms-style "Schlusstermin für den Eingang der Angebote".
_DEADLINE_LABEL = (
    r"(?:Angebotsfrist|Teilnahmeantragsfrist|Bewerbungsfrist|Angebotsabgabefrist"
    r"|Schlusstermin[^:\n]{0,60}|Frist f[üu]r den Eingang[^:\n]{0,40})"
)
_DEADLINE_RE = re.compile(
    _DEADLINE_LABEL + r"\s*:?\s*(\d{1,2})\.(\d{1,2})\.(\d{4})" r"(?:[,\s]+(\d{1,2})[:.](\d{2}))?",
    re.IGNORECASE,
)


class OeffentlicheVergabeConnector(TenderConnector):
    source_name = "oeffentlichevergabe"
    display_name = "Datenservice Öffentlicher Einkauf (DE)"
    homepage = "https://oeffentlichevergabe.de"
    # ~1,000-1,200 notices a day and no server-side keyword search, so the feed is
    # prefiltered client-side. Without this a single sweep would store the whole of
    # German public procurement.
    prefilter = True
    notes = (
        "Daily CC0 OCDS export, one ZIP per publication day, no authentication. "
        "The only source carrying German below-EU-threshold (UVgO/VOB) notices. "
        "Deadlines are parsed out of the notice prose because UVgO notices carry no "
        "tenderPeriod field."
    )

    async def fetch(self, date_from: datetime, date_to: datetime) -> list[NormalizedTender]:
        date_from, date_to = self.clamp_window(date_from, date_to)
        days = _days(date_from, date_to, limit=self.settings.max_pages_per_source)
        out: list[NormalizedTender] = []
        seen: set[str] = set()
        async with self.client(headers={"Accept": "application/zip"}) as client:
            for index, day in enumerate(days):
                releases = await self._fetch_day(client, day)
                kept = 0
                for release in releases:
                    tender = self._normalize(release)
                    if tender and tender.source_notice_id not in seen:
                        seen.add(tender.source_notice_id)
                        out.append(tender)
                        kept += 1
                self.log_progress(
                    page=index + 1,
                    pub_day=day.strftime("%Y-%m-%d"),
                    received=len(releases),
                    kept=kept,
                )
        return out

    # -- http ---------------------------------------------------------------
    async def _fetch_day(self, client: httpx.AsyncClient, day: datetime) -> list[dict[str, Any]]:
        """One publication day. A day with no export is normal, not a failure."""
        params = {"pubDay": day.strftime("%Y-%m-%d"), "format": EXPORT_FORMAT}
        attempts = max(1, self.settings.max_retries + 1)
        last_error: Exception | None = None
        for attempt in range(attempts):
            try:
                response = await client.get(API_URL, params=params)
            except httpx.HTTPError as exc:  # transport-level, worth a retry
                last_error = exc
                await self._sleep(2**attempt)
                continue
            if response.status_code in (404, 204):
                return []
            if response.status_code == 429 or response.status_code >= 500:
                last_error = ConnectorError(
                    self.source_name,
                    f"HTTP {response.status_code} from source",
                    status=response.status_code,
                    url=str(response.url),
                    retryable=True,
                )
                await self._sleep(2**attempt)
                continue
            if response.status_code >= 400:
                raise ConnectorError(
                    self.source_name,
                    f"HTTP {response.status_code} from source",
                    status=response.status_code,
                    url=str(response.url),
                )
            body = response.content
            if len(body) > self.settings.max_response_bytes:
                raise ConnectorError(
                    self.source_name,
                    f"response too large ({len(body)} bytes > {self.settings.max_response_bytes})",
                    url=str(response.url),
                )
            if not body:
                return []
            if not body.startswith(_ZIP_MAGIC):
                raise ConnectorError(
                    self.source_name,
                    "expected a ZIP export but the body is not a ZIP archive",
                    url=str(response.url),
                )
            return _releases_from_zip(body)
        if isinstance(last_error, ConnectorError):
            raise last_error
        raise ConnectorError(self.source_name, f"request failed after {attempts} attempts: {last_error}")

    # -- normalization ------------------------------------------------------
    def _normalize(self, release: dict[str, Any]) -> NormalizedTender | None:
        tender_node = release.get("tender") or {}
        title = tender_node.get("title") or ""
        description = tender_node.get("description") or ""
        # Prefilter before normalizing: this feed is a whole country's procurement.
        if not self.keep(title, description, (release.get("buyer") or {}).get("name")):
            return None
        tender = normalize_release(release, source=self.source_name)
        if not tender:
            return None
        tender.buyer_country = _country(tender_node) or "DEU"
        tender.language = release.get("language") or "deu"
        if tender.deadline is None:
            tender.deadline = _deadline_from_text(description)
        if not tender.source_url:
            tender.source_url = _first_document_url(tender_node)
        return tender


# -- helpers ----------------------------------------------------------------
def _days(date_from: datetime, date_to: datetime, *, limit: int) -> list[datetime]:
    """Publication days in the window, newest first so a cap keeps the recent end."""
    span = (date_to.date() - date_from.date()).days
    days = [
        datetime.combine(date_to.date() - timedelta(days=offset), datetime.min.time())
        for offset in range(max(0, span) + 1)
    ]
    return days[: max(1, limit)]


def _releases_from_zip(body: bytes) -> list[dict[str, Any]]:
    """Flatten every OCDS release in the archive. One bad member must not drop the day.

    A truncated response still starts with the ZIP magic bytes, so the magic check
    in `_fetch_day` does not catch it - the archive only fails when the central
    directory is read. That is a transport failure worth retrying, not a crash.
    """
    releases: list[dict[str, Any]] = []
    try:
        archive = zipfile.ZipFile(io.BytesIO(body))
    except zipfile.BadZipFile as exc:
        raise ConnectorError(
            "oeffentlichevergabe",
            f"export is not a readable ZIP archive ({exc}) - likely a truncated download",
            retryable=True,
        ) from exc
    with archive:
        for name in archive.namelist():
            if name.endswith("/"):
                continue
            try:
                payload = json.loads(archive.read(name).decode("utf-8", "replace"))
            except (ValueError, OSError, zipfile.BadZipFile):
                continue
            if isinstance(payload, dict) and isinstance(payload.get("releases"), list):
                releases.extend(r for r in payload["releases"] if isinstance(r, dict))
            elif isinstance(payload, dict):
                releases.append(payload)
    return releases


def _country(tender_node: dict[str, Any]) -> str | None:
    for item in tender_node.get("items") or []:
        code = ((item or {}).get("deliveryAddress") or {}).get("countryName")
        if code:
            return str(code).upper()[:3]
    return None


def _first_document_url(tender_node: dict[str, Any]) -> str | None:
    for doc in tender_node.get("documents") or []:
        if isinstance(doc, dict) and doc.get("url"):
            return str(doc["url"])
    return None


def _deadline_from_text(description: str | None) -> datetime | None:
    """Pull the submission deadline out of the notice prose (UVgO has no field)."""
    if not description:
        return None
    match = _DEADLINE_RE.search(description)
    if not match:
        return None
    day, month, year, hour, minute = match.groups()
    try:
        return datetime(int(year), int(month), int(day), int(hour or 0), int(minute or 0))
    except ValueError:  # 31.02.2026 and friends
        return None
