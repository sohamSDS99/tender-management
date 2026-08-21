"""The frontend's TypeScript interfaces must match what the API actually sends.

TypeScript trusts its own declarations, so a field the frontend declares but the
API never returns type-checks perfectly and arrives as undefined at runtime.
That already happened once: `FetchRun.batch_id` was added to the ORM model and
to the TS interface, but not to the response schema, so the dashboard's declared
type was lying about the payload.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.schemas import (
    AutomationStatus,
    FetchRunSchema,
    SourceStatus,
    StatsResponse,
    TenderDetail,
    TenderListItem,
)

TYPES_FILE = Path(__file__).resolve().parents[2] / "frontend" / "src" / "types" / "index.ts"

# TS interface name -> the response model it is meant to mirror.
PAIRS = [
    ("Tender", TenderListItem),
    ("TenderDetail", TenderDetail),
    ("FetchRun", FetchRunSchema),
    ("SourceStatus", SourceStatus),
    ("Stats", StatsResponse),
    ("AutomationStatus", AutomationStatus),
]


def ts_fields(name: str) -> set[str]:
    body = re.search(
        rf"export interface {name}(?: extends \w+)?\s*\{{(.*?)\n\}}",
        TYPES_FILE.read_text(encoding="utf-8"),
        re.S,
    )
    assert body, f"interface {name} not found in {TYPES_FILE}"
    return set(re.findall(r"^\s{2}(\w+)\??:", body.group(1), re.M))


def test_types_file_exists() -> None:
    assert TYPES_FILE.is_file()


@pytest.mark.parametrize(("ts_name", "model"), PAIRS, ids=[p[0] for p in PAIRS])
def test_frontend_declares_no_field_the_api_does_not_send(ts_name, model) -> None:
    declared = ts_fields(ts_name)
    if ts_name == "TenderDetail":
        declared |= ts_fields("Tender")  # `extends Tender`
    phantom = declared - set(model.model_fields)
    assert not phantom, (
        f"frontend type {ts_name} declares {sorted(phantom)}, which {model.__name__} "
        "never returns - those arrive as undefined at runtime"
    )
