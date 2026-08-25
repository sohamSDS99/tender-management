"""Try a candidate source before it is saved.

Two jobs, and the first one is not optional.

**Refusing unsafe addresses.** This endpoint makes the *server* fetch a URL a
dashboard user typed, and the dashboard is unauthenticated by design (D23).
That decision was sound for the two expensive writes it covered - they are
expensive, not confidential, so rate limits are the right control. A
server-side fetcher is a different class of thing: it can reach the internal
network, the cloud metadata endpoint, and localhost. So the guard here is a
condition of the feature existing, not a hardening pass to do later.

**Reporting what parsed, not what answered.** A 200 proves the credential
works and nothing else. A source that answers but yields no notices is exactly
the failure this system spent a day chasing on SAM.gov, so the probe counts
what came out and refuses to call an empty result a success.
"""

from __future__ import annotations

import ipaddress
import logging
import socket
from typing import Any
from urllib.parse import urlparse

import httpx

from app.connectors.generic import MappingError, apply_mapping, describe_paths, records_from
from app.logging_config import log_ctx
from app.settings import Settings

logger = logging.getLogger(__name__)

#: Redirects are followed, but each hop is re-checked: a public host is free to
#: redirect to 169.254.169.254, and following that blindly would defeat the
#: whole guard.
MAX_REDIRECTS = 3


class UnsafeUrl(ValueError):
    """The URL must not be fetched. Message is written for the person who typed it."""


def _resolve(host: str) -> list[str]:
    """Every address a host resolves to. Patched in tests; see the guard below."""
    try:
        return [info[4][0] for info in socket.getaddrinfo(host, None)]
    except socket.gaierror:
        return []


def assert_safe_url(url: str) -> None:
    """Raise UnsafeUrl unless this is a public https address."""
    parsed = urlparse(url)

    if parsed.scheme != "https":
        raise UnsafeUrl(
            "The endpoint must be an https:// URL. Anything else is refused, "
            "including http:// — a key sent in clear text is a key given away."
        )
    host = parsed.hostname
    if not host:
        raise UnsafeUrl("That URL has no hostname.")

    addresses = _resolve(host)
    if not addresses:
        raise UnsafeUrl(f"'{host}' does not resolve. Check the address for a typo.")

    for raw in addresses:
        try:
            ip = ipaddress.ip_address(raw)
        except ValueError:  # pragma: no cover - getaddrinfo returns valid addresses
            raise UnsafeUrl(f"'{host}' resolved to something unusable.") from None
        # is_global is false for loopback, link-local, private, reserved and
        # multicast in one check, which is the whole list we care about — and it
        # will not drift the way an enumerated set of ranges would.
        if not ip.is_global:
            raise UnsafeUrl(
                f"'{host}' resolves to {ip}, which is a private or local address. "
                "Sources must be on the public internet."
            )


# --- what the response actually is -----------------------------------------


def detect_format(payload: Any) -> str:
    """'ocds' | 'rss' | 'json' | 'unknown', from the shape of one response.

    Auto-detection is what makes the common case zero-configuration: an OCDS
    portal needs no mapping at all, because normalize_release already knows the
    schema. Anything unrecognised falls through to 'json' and is mapped by hand.
    """
    if isinstance(payload, dict):
        releases = payload.get("releases")
        if isinstance(releases, list) and any(
            isinstance(r, dict) and ("ocid" in r or "tender" in r) for r in releases
        ):
            return "ocds"
        if any(key in payload for key in ("rss", "feed", "channel")):
            return "rss"
        return "json"
    if isinstance(payload, list):
        return "json"
    return "unknown"


def guess_records_path(payload: Any) -> str:
    """Where the notices probably live, so the field picker opens on something.

    A guess, offered rather than imposed: the operator can point it elsewhere.
    Picks the array holding the most objects, because that is what a page of
    notices looks like beside a list of facets or links.
    """
    if isinstance(payload, list):
        return ""
    if not isinstance(payload, dict):
        return ""

    # describe_paths reports leaves ("data.items[].id"), so the array paths are
    # its prefixes: everything up to and including each "[]".
    candidates: set[str] = set()
    for path, _sample in describe_paths(payload):
        parts = path.split(".")
        for index, part in enumerate(parts):
            if part.endswith("[]"):
                candidates.add(".".join(parts[: index + 1]))

    best: tuple[int, str] = (0, "")
    for path in candidates:
        found = records_from(payload, path)
        if len(found) > best[0]:
            best = (len(found), path)
    return best[1]


async def probe_source(
    url: str,
    settings: Settings,
    *,
    credential: str | None = None,
    auth: str = "none",
    auth_param: str | None = None,
    mapping: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Fetch one page and report what could be read out of it.

    Stores nothing. Returns counts, the detected format, and the paths a field
    picker can offer — everything needed to decide whether this source is worth
    saving, before it is.
    """
    assert_safe_url(url)

    params: dict[str, str] = {}
    headers: dict[str, str] = {}
    if credential and auth == "query":
        params[auth_param or "api_key"] = credential
    elif credential and auth == "header":
        headers[auth_param or "X-Api-Key"] = credential
    elif credential and auth == "bearer":
        headers["Authorization"] = f"Bearer {credential}"

    async with httpx.AsyncClient(
        timeout=settings.request_timeout_seconds,
        follow_redirects=False,
        headers={"User-Agent": settings.user_agent},
    ) as client:
        response = await _fetch_checked(client, url, params, headers)

    status = response.status_code
    if status != 200:
        return {
            "ok": False,
            "status": status,
            "reason": "http",
            # Enough to tell a bad key from a bad URL, and short enough that a
            # server's error page does not become the whole screen.
            "detail": response.text[:200],
        }

    try:
        payload = response.json()
    except ValueError:
        return {
            "ok": False,
            "status": status,
            "reason": "not_json",
            "detail": (
                "The endpoint answered, but the response is not JSON. XML and CSV "
                "feeds need a connector rather than a mapping."
            ),
        }

    detected = detect_format(payload)
    records_path = str((mapping or {}).get("records") or "") or guess_records_path(payload)
    records = records_from(payload, records_path)

    parsed = 0
    sample: dict[str, Any] | None = None
    if mapping:
        for record in records:
            try:
                apply_mapping(record, mapping, source="probe")
            except MappingError:
                continue
            parsed += 1
    if records:
        sample = records[0]

    log_ctx(logger, logging.INFO, "source probed", status=status, found=len(records), parsed=parsed)
    return {
        "ok": bool(records),
        "status": status,
        "format": detected,
        "records_path": records_path,
        "found": len(records),
        "parsed": parsed if mapping else None,
        "paths": [{"path": p, "sample": _short(v)} for p, v in describe_paths(sample or {})],
        "reason": None if records else "no_records",
        "detail": None
        if records
        else (
            "The endpoint answered, but nothing in the response looks like a list "
            "of notices. Point 'records' at the right array below, or check the URL."
        ),
    }


async def _fetch_checked(
    client: httpx.AsyncClient, url: str, params: dict[str, str], headers: dict[str, str]
) -> httpx.Response:
    """GET, following redirects by hand so every hop is re-checked.

    httpx's own follow_redirects would take us to whatever the server names,
    and a public host is free to redirect to 169.254.169.254.
    """
    for _hop in range(MAX_REDIRECTS + 1):
        response = await client.get(url, params=params, headers=headers)
        if response.status_code not in (301, 302, 303, 307, 308):
            return response
        location = response.headers.get("location")
        if not location:
            return response
        url = str(httpx.URL(url).join(location))
        assert_safe_url(url)
        params = {}
    raise UnsafeUrl("That URL redirects too many times.")


def _short(value: Any) -> str:
    text = "" if value is None else str(value)
    return text[:120]
