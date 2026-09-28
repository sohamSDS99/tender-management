"""Tender Impulse - a global aggregator, paged by id and encrypted on the wire.

    GET {TENDER_IMPULSE_API_URL}?lastid={lastid}
    Authorization: Bearer {TENDER_IMPULSE_ACCESS_TOKEN}

Requires TENDER_IMPULSE_ACCESS_TOKEN *and* TENDER_IMPULSE_ENCRYPTION_KEY, both
issued by Tender Impulse on a paid agreement, plus the `lastid` to start from.
Docs: https://tenderimpulse.com/api-documentation; reference clients in four
languages at https://github.com/tenderimpulse.

THE ONLY SOURCE HERE WITH NO DATE WINDOW
    Every other connector is asked for a window and answers with what was
    published in it. This API has no date parameter at all. A call takes a
    `lastid` and returns the next batch of records after it, plus a `fetchid`
    (the id of the last record in that batch) to send as the next `lastid`. An
    empty batch means caught up, and its `fetchid` comes back equal to the
    `lastid` sent.

    So the window `fetch` is handed is ignored, and what decides coverage is a
    bookmark: the last `fetchid` whose batch this system has *stored*. It lives
    in app_settings (services/cursors.py), and ingest moves it only after
    `store_tenders` returns. The vendor is explicit about why that order
    matters: store the bookmark first, fail, and those records are skipped for
    good - there is no way to ask for them again. So this module never writes
    it; it reports `next_cursor` and ingest decides.

    The consequence worth knowing: a dashboard sweep "30 days back" and a
    scheduled "72 hours back" do exactly the same thing here - read forward
    from the bookmark. Nothing before the first stored bookmark is reachable
    except by setting TENDER_IMPULSE_START_ID lower and clearing the bookmark.

EVERY RESPONSE IS ENCRYPTED, AND THE CHECKSUM IS NOT OPTIONAL
    The body is `{"data": "<b64 ciphertext>:<b64 iv>", "crc": "<md5 hex>"}`.
    `data` is AES-128-CBC under the encryption key - taken as UTF-8, padded
    with ASCII "0" or truncated to 16 bytes, exactly as all four reference
    clients do - with PKCS#7 padding. The MD5 of the plaintext must equal
    `crc`; a mismatch is a transmission error and the batch is discarded,
    never half-believed. Only then is the plaintext parsed, and a
    `status: "error"` inside it (an expired token, a `lastid` past the
    server's maximum) is a failure even though the HTTP status was 200.

THE FEED IS TAKEN WHOLE, AND FILTERED HERE
    Tender Impulse advertises ~40,000 notices a day and offers no search, so
    `keep()` applies this repo's PREFILTER_TERMS to title, buyer and
    `other_information` (the only free-text field). The field list is a first
    guess, not a measurement: D40's rule is to tune a prefilter on a raw day of
    the feed, and no raw day existed when this was written. Records dropped
    here are still behind the bookmark, so widening the terms later does not
    recover them.

    TENDER_IMPULSE_STORE_UNFILTERED is the deliberately named escape hatch, and
    APPLY_KEYWORD_PREFILTER does not reach it - same reasoning as Spend Network.

WHAT THE REFERENCE CLIENT DOES THAT THIS DOES NOT
    It downloads every tender's document to local disk. At 40,000 a day that is
    a disk, not a feature; the `filepath` URL is kept as a document link and
    nothing is downloaded.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from app.connectors.base import (
    STAGE_TENDER,
    ConnectorError,
    NormalizedTender,
    TenderConnector,
    _redact,
    parse_datetime,
    status_from_deadline,
)
from app.connectors.keywords import looks_relevant
from app.logging_config import log_ctx

logger = logging.getLogger(__name__)

#: AES-128: the key is exactly this many bytes after padding or truncation.
KEY_BYTES = 16
_DATE_FORMATS = ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d %b %Y", "%d %B %Y", "%Y-%m-%d %H:%M:%S")
_CPV = re.compile(r"^\s*(\d{8})(?:-\d)?\s*(?::\s*(.*?))?\s*$")
_AMOUNT = re.compile(r"^\s*([A-Z]{3})?\s*([\d,]+(?:\.\d+)?)\s*([A-Z]{3})?\s*$")


@dataclass
class _Sweep:
    requests: int = 0
    received: int = 0
    kept: int = 0
    truncated: bool = False
    stopped_early: str | None = None


def decrypt(data: str, key: str) -> str:
    """`<b64 ciphertext>:<b64 iv>` -> plaintext, the way every reference client does it."""
    parts = str(data).split(":")
    if len(parts) != 2:
        raise ValueError("encrypted payload is not '<ciphertext>:<iv>'")
    ciphertext, iv = base64.b64decode(parts[0]), base64.b64decode(parts[1])
    raw_key = key.encode("utf-8").ljust(KEY_BYTES, b"0")[:KEY_BYTES]
    decryptor = Cipher(algorithms.AES(raw_key), modes.CBC(iv)).decryptor()
    padded = decryptor.update(ciphertext) + decryptor.finalize()
    unpadder = padding.PKCS7(algorithms.AES.block_size).unpadder()
    return (unpadder.update(padded) + unpadder.finalize()).decode("utf-8")


class TenderImpulseConnector(TenderConnector):
    source_name = "tender_impulse"
    display_name = "Tender Impulse (global)"
    homepage = "https://tenderimpulse.com"
    requires_api_key = True
    credential_label = "access token"
    credential_extra_field = "tender_impulse_encryption_key"
    credential_extra_label = "Encryption key"
    credential_extra_hint = (
        "Issued by Tender Impulse with the access token. Every response is encrypted with it, "
        "so a token without this key fetches nothing readable."
    )
    credential_extra_placeholder = "Paste the encryption key"
    #: The third thing Tender Impulse issues, on the card beside the other two
    #: (D39: a control a page away from its source is a control nobody finds).
    setup_fields = (
        {
            "field": "tender_impulse_start_id",
            "label": "Starting id",
            "hint": (
                "The lastid Tender Impulse gives you to start from. Read only until the first "
                "batch is stored; after that the source resumes from its own bookmark."
            ),
            "placeholder": "8156393",
        },
    )
    prefilter = True
    notes = (
        "A paid global aggregator (~40,000 notices a day). Pages by id, not by date: each "
        "sweep reads forward from the last batch stored, so the lookback window does not "
        "apply. Responses are AES-encrypted and checksum-verified. Filtered here on the "
        "shared term list; needs a starting id (TENDER_IMPULSE_START_ID) on first run."
    )

    #: Where the bookmark is overlaid from (services/cursors.py).
    cursor_field = "tender_impulse_cursor"
    #: Written by `fetch`, read by ingest after the batch is stored. None = do not move.
    next_cursor: str | None = None

    def unavailable_reason(self) -> str | None:
        if not self.settings.tender_impulse_access_token:
            return (
                "TENDER_IMPULSE_ACCESS_TOKEN is not set - paste the access token Tender Impulse "
                "issued into the box on this card."
            )
        if not self.settings.tender_impulse_encryption_key:
            return (
                "TENDER_IMPULSE_ENCRYPTION_KEY is not set - paste it in the 'Encryption key' box "
                "on this card. Every response is encrypted, so the token alone reads nothing."
            )
        if not _as_id(self.settings.tender_impulse_cursor) and not _as_id(
            self.settings.tender_impulse_start_id
        ):
            return (
                "TENDER_IMPULSE_START_ID is not set - Tender Impulse supplies the id to start "
                "from with the credentials; paste it in the 'Starting id' box on this card. It is "
                "only needed once: after the first stored batch the connector resumes on its own."
            )
        return None

    def keep(self, *texts: str | None) -> bool:
        """Not switchable by APPLY_KEYWORD_PREFILTER - see the module docstring."""
        if self.settings.tender_impulse_store_unfiltered:
            return True
        return looks_relevant(*texts)

    async def fetch(self, date_from: datetime, date_to: datetime) -> list[NormalizedTender]:
        # The window is ignored on purpose: this API has no date parameter.
        last_id = _as_id(self.settings.tender_impulse_cursor) or _as_id(self.settings.tender_impulse_start_id)
        if last_id is None:
            raise ConnectorError(self.source_name, "no bookmark and no TENDER_IMPULSE_START_ID to start from")
        state = _Sweep()
        budget = max(1, self.settings.tender_impulse_max_requests_per_sweep)
        out: list[NormalizedTender] = []
        seen: set[str] = set()
        cursor = last_id

        async with self.client(
            headers={
                "Authorization": f"Bearer {self.settings.tender_impulse_access_token}",
                "Content-Type": "application/json",
            },
            timeout=90,
        ) as client:
            while True:
                if state.requests >= budget:
                    # The next sweep resumes from `cursor`, so nothing is lost -
                    # but a backlog this deep must not read as caught up.
                    state.truncated = True
                    break
                if state.requests:
                    await self._sleep(self.settings.tender_impulse_page_pause_seconds)
                try:
                    records, fetch_id = await self._batch(client, cursor)
                except ConnectorError as exc:
                    if not state.requests:
                        raise
                    # What was already read is kept and the bookmark stops at
                    # the last complete batch; the next sweep retries this one.
                    state.stopped_early = exc.message
                    break
                finally:
                    state.requests += 1
                if not records:
                    break
                if fetch_id is None or fetch_id <= cursor:
                    state.stopped_early = f"fetchid {fetch_id} did not advance past {cursor}"
                    break
                state.received += len(records)
                for raw in records:
                    if not isinstance(raw, dict):
                        continue
                    if not self.keep(
                        raw.get("title"), raw.get("authority_name"), raw.get("other_information")
                    ):
                        continue
                    try:
                        tender = self._normalize(raw)
                    except Exception:
                        continue
                    if tender and tender.source_notice_id not in seen:
                        seen.add(tender.source_notice_id)
                        out.append(tender)
                cursor = fetch_id

        state.kept = len(out)
        self.next_cursor = str(cursor) if cursor != last_id else None
        log_ctx(
            logger,
            logging.WARNING if state.stopped_early else logging.INFO,
            "source progress",
            source=self.source_name,
            from_id=last_id,
            to_id=cursor,
            requests=state.requests,
            received=state.received,
            kept=state.kept,
            truncated=state.truncated,
            stopped_early=state.stopped_early,
        )
        return out

    async def _batch(self, client: Any, last_id: int) -> tuple[list[Any], int | None]:
        url = self.settings.tender_impulse_api_url
        try:
            envelope = await self.request(
                client,
                "GET",
                url,
                params={"lastid": last_id},
                expect="json",
                # PHP answers with text/html whatever the body is.
                content_types=("json", "html", "text"),
            )
        except ConnectorError as exc:
            if exc.status == 401:
                raise ConnectorError(
                    self.source_name,
                    "Tender Impulse refused the access token (HTTP 401) - check the token on this card.",
                    status=401,
                    url=url,
                ) from exc
            raise
        if not isinstance(envelope, dict) or "data" not in envelope or "crc" not in envelope:
            raise ConnectorError(
                self.source_name, "response is not an encrypted {data, crc} envelope", url=url
            )
        try:
            plaintext = decrypt(envelope["data"], self.settings.tender_impulse_encryption_key)
        except (ValueError, TypeError) as exc:
            raise ConnectorError(
                self.source_name,
                "could not decrypt the response - check the encryption key on this card",
                url=url,
            ) from exc
        if hashlib.md5(plaintext.encode("utf-8")).hexdigest().lower() != str(envelope["crc"]).lower():
            raise ConnectorError(
                self.source_name, "checksum mismatch - batch discarded", url=url, retryable=True
            )
        try:
            details = json.loads(plaintext)
        except ValueError as exc:
            raise ConnectorError(self.source_name, f"decrypted payload is not JSON: {exc}", url=url) from exc
        if details.get("status") != "success":
            raise ConnectorError(
                self.source_name, f"Tender Impulse error: {details.get('msg') or 'no message'}", url=url
            )
        return list(details.get("tenders") or []), _as_id(details.get("fetchid"))

    def _normalize(self, raw: dict[str, Any]) -> NormalizedTender | None:
        tender_id = raw.get("tender_id")
        if tender_id in (None, ""):
            return None
        deadline, deadline_tz = parse_datetime(_text(raw.get("deadline")), _DATE_FORMATS)
        value, currency = _amount(raw.get("value_of_contract"))
        document = _url(raw.get("filepath"))
        notice_type = _text(raw.get("contract_type"))
        return NormalizedTender(
            source=self.source_name,
            source_notice_id=str(tender_id),
            # `web` is the authority's or the source portal's site - the nearest
            # thing to an original notice this API carries.
            source_url=_url(raw.get("web")),
            reference_number=_text(raw.get("reference")),
            title=_text(raw.get("title")) or f"Tender Impulse {tender_id}",
            description=_text(raw.get("other_information")),
            buyer_name=_text(raw.get("authority_name")),
            # A country name ("Australia"), not ISO-2 - stored as given, as
            # World Bank does, rather than guessed at.
            buyer_country=_text(raw.get("country")),
            delivery_location=_text(raw.get("location")),
            deadline=deadline,
            source_timezone=deadline_tz,
            status=status_from_deadline(deadline),
            procurement_stage=STAGE_TENDER,
            notice_type=notice_type,
            estimated_value=value,
            currency=currency,
            classification_codes=_cpv_codes(raw.get("cpv_codes")),
            document_urls=[document] if document else [],
            raw_payload=_scrub_payload(raw),
        )


def _as_id(value: Any) -> int | None:
    try:
        return int(str(value).strip()) if value not in (None, "") else None
    except ValueError:
        return None


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def _url(value: Any) -> str | None:
    text = _text(value)
    if not text or not text.lower().startswith(("http://", "https://")):
        return None
    return _redact(text)


def _scrub_payload(raw: dict[str, Any]) -> dict[str, Any]:
    """The stored payload keeps every fact; any credential in a URL is masked first."""
    out = dict(raw)
    for field in ("filepath", "web"):
        if isinstance(out.get(field), str):
            out[field] = _redact(out[field])
    return out


def _cpv_codes(value: Any) -> list[dict[str, Any]]:
    """`"35410000 : Armoured military vehicles,35610000 : Military aircrafts"` -> CPV entries.

    The format is the one the docs show for the News API's `cpvs`, which they
    say the Tender API shares. Anything that is not an 8-digit code is dropped
    rather than stored as a code.
    """
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for part in str(value or "").split(","):
        match = _CPV.match(part)
        if not match or match.group(1) in seen:
            continue
        seen.add(match.group(1))
        entry: dict[str, Any] = {"scheme": "CPV", "code": match.group(1)}
        if match.group(2):
            entry["description"] = match.group(2)
        out.append(entry)
    return out


def _amount(value: Any) -> tuple[float | None, str | None]:
    """Only a plain figure, optionally with an ISO currency code either side.

    `value_of_contract` is free text in the docs ("..."), and "Refer document"
    or "1-2 million" is not a number - a wrong estimate is worse than none.
    """
    match = _AMOUNT.match(str(value or ""))
    if not match:
        return None, None
    try:
        amount = float(match.group(2).replace(",", ""))
    except ValueError:
        return None, None
    return amount, match.group(1) or match.group(3)
