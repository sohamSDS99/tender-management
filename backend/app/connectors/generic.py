"""A source described by data instead of programmed.

The eight built-ins parse one portal each. This parses whatever it is pointed
at, given a mapping from that portal's field names to
``NormalizedTender``'s - which is what lets somebody add a feed nobody
anticipated without shipping a release.

Paths are dotted, with ``[]`` marking an array to walk:
``data.items[].tender.title``. Deliberately not JSONPath: the full grammar
brings filters, wildcards and recursive descent, none of which a field-picker
UI can present, and all of which are more ways for a mapping to be subtly
wrong.

A missing path yields None rather than raising. Portals omit optional fields on
individual notices all the time, and one sparse record must not fail a sweep.
"""

from __future__ import annotations

import logging
from typing import Any

from app.connectors.base import NormalizedTender, TenderConnector, parse_datetime
from app.models import Source
from app.settings import Settings

logger = logging.getLogger(__name__)

#: Without these a record cannot become a tender: source_notice_id is half the
#: dedupe key, and a notice with no title cannot be scored or recognised.
REQUIRED_FIELDS = ("source_notice_id", "title")

#: Fields a mapping may set. Anything else in a mapping is ignored rather than
#: rejected, so a mapping written against a later version still loads.
MAPPABLE = (
    "source_notice_id",
    "title",
    "description",
    "source_url",
    "reference_number",
    "buyer_name",
    "buyer_country",
    "delivery_location",
    "publication_date",
    "deadline",
    "status",
    "notice_type",
    "estimated_value",
    "currency",
    "language",
)

#: Parsed as datetimes rather than passed through as strings.
DATE_FIELDS = ("publication_date", "deadline")

#: How deep describe_paths will walk. A pathological payload should not turn
#: the field picker into a thousand-row list nobody can use.
MAX_DEPTH = 8
#: How many paths to report at most, for the same reason.
MAX_PATHS = 200


class MappingError(ValueError):
    """Raised when a record cannot become a tender. Message is for a human."""


def extract_path(node: Any, path: str) -> Any:
    """Read a dotted path. ``[]`` walks into a list and collects.

    Returns None for anything absent, so callers can treat "not present" and
    "present but empty" the same way - which is what portals actually do.
    """
    if not path:
        return None
    head, _, rest = path.partition(".")
    collect = head.endswith("[]")
    key = head[:-2] if collect else head

    if key:
        if not isinstance(node, dict):
            return None
        node = node.get(key)

    if collect:
        if not isinstance(node, list):
            return None
        return node if not rest else [extract_path(item, rest) for item in node]

    return node if not rest else extract_path(node, rest)


def records_from(payload: Any, path: str) -> list[dict[str, Any]]:
    """The list of notices inside a response, or empty when there is none."""
    if not path:
        return payload if isinstance(payload, list) else []
    found = extract_path(payload, path)
    if not isinstance(found, list):
        return []
    return [item for item in found if isinstance(item, dict)]


def apply_mapping(record: dict[str, Any], mapping: dict[str, str], *, source: str) -> NormalizedTender:
    """Turn one record into a tender, or explain why it cannot be one."""
    values: dict[str, Any] = {}
    for field in MAPPABLE:
        path = mapping.get(field)
        if not path:
            continue
        raw = extract_path(record, path)
        if raw is None or raw == "":
            continue
        if field in DATE_FIELDS:
            parsed, _tz = parse_datetime(raw)
            if parsed is not None:
                values[field] = parsed
            continue
        if field == "estimated_value":
            try:
                values[field] = float(raw)
            except (TypeError, ValueError):
                pass
            continue
        values[field] = str(raw).strip()

    for field in REQUIRED_FIELDS:
        if not values.get(field):
            raise MappingError(
                f"No {field.replace('_', ' ')} at '{mapping.get(field) or '(unmapped)'}'. "
                "Check the field mapping against a sample record."
            )

    return NormalizedTender(
        source=source,
        # The mapping may be wrong. Keeping the original record is what makes a
        # mis-mapped source diagnosable rather than merely empty.
        raw_payload=record,
        **values,
    )


def describe_paths(node: Any, prefix: str = "", depth: int = 0) -> list[tuple[str, Any]]:
    """Every readable path in a sample, with the value found there.

    This is what the field picker offers: real paths from a real response,
    rather than a text box and a guess.
    """
    out: list[tuple[str, Any]] = []
    if depth > MAX_DEPTH or len(out) > MAX_PATHS:
        return out
    if isinstance(node, dict):
        for key, value in node.items():
            path = f"{prefix}.{key}" if prefix else key
            if isinstance(value, dict):
                out.extend(describe_paths(value, path, depth + 1))
            elif isinstance(value, list):
                first = next((v for v in value if isinstance(v, dict)), None)
                if first is not None:
                    out.extend(describe_paths(first, f"{path}[]", depth + 1))
                elif value:
                    out.append((f"{path}[]", value[0]))
            else:
                out.append((path, value))
            if len(out) >= MAX_PATHS:
                break
    return out[:MAX_PATHS]


# --- the connector ---------------------------------------------------------


class GenericConnector(TenderConnector):
    """A source built from a ``Source`` row rather than from a module.

    Everything portal-specific - the URL, how the credential is presented, where
    the notices live in the response, what the fields are called - comes off the
    row. What stays here is the part every source shares: fetch, parse, filter,
    normalise.
    """

    def __init__(
        self,
        row: Source,
        settings: Settings,
        credential: str | None = None,
        transport: object | None = None,
    ) -> None:
        super().__init__(settings, transport=transport)  # type: ignore[arg-type]
        self.row = row
        self.credential = credential
        self.source_name = row.name
        self.display_name = row.display_name
        self.homepage = row.homepage or ""
        self.requires_api_key = row.auth != "none"
        self.notes = row.notes or ""
        # A source described by data has no server-side keyword search we can
        # rely on, so it is prefiltered locally like the other feeds without one.
        self.prefilter = True

    def unavailable_reason(self) -> str | None:
        if self.row.auth != "none" and not self.credential:
            return f"No API key set for {self.display_name}."
        if not self.row.url:
            return "No endpoint URL configured."
        return None

    @property
    def enabled(self) -> bool:
        return bool(self.row.enabled)

    def _auth(self) -> tuple[dict[str, str], dict[str, str]]:
        """Credential as (params, headers), by the style the row declares."""
        params: dict[str, str] = {}
        headers: dict[str, str] = {}
        if not self.credential or self.row.auth == "none":
            return params, headers
        if self.row.auth == "query":
            params[self.row.auth_param or "api_key"] = self.credential
        elif self.row.auth == "header":
            headers[self.row.auth_param or "X-Api-Key"] = self.credential
        elif self.row.auth == "bearer":
            headers["Authorization"] = f"Bearer {self.credential}"
        return params, headers

    def parse(self, payload: object) -> list[NormalizedTender]:
        """Response -> tenders. Separated from fetching so the probe reuses it."""
        mapping = dict(self.row.mapping or {})
        records = records_from(payload, str(mapping.get("records") or ""))
        out: list[NormalizedTender] = []
        for record in records:
            try:
                tender = apply_mapping(record, mapping, source=self.source_name)
            except MappingError:
                # One unmappable record loses itself, not the batch. A source
                # where *every* record fails is caught by the probe before it is
                # ever saved.
                continue
            if not self.keep(tender.title, tender.description, tender.buyer_name):
                continue
            out.append(tender)
        return out

    async def fetch(self, date_from, date_to) -> list[NormalizedTender]:  # type: ignore[no-untyped-def]
        date_from, date_to = self.clamp_window(date_from, date_to)
        params, headers = self._auth()
        async with self.client() as client:
            data = await self.request(
                client, "GET", self.row.url, params=params, headers=headers, expect="json"
            )
        tenders = self.parse(data)
        self.log_progress(page=1, received=len(tenders))
        return tenders
