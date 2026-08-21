"""Every value the app stores must fit the column it is stored in.

SQLite ignores VARCHAR length limits; PostgreSQL enforces them. That difference
hid a real defect: `buyer_country` was varchar(8), but the World Bank feed emits
full country names ("Indonesia"), so on PostgreSQL those notices were dropped
one at a time by store_tenders' per-record guard - a silent partial data loss
that every SQLite test passed straight through.

These tests are engine-independent: they compare fixture values against the
declared column widths, so the whole class of bug fails in the fast suite
instead of only in production.
"""

from __future__ import annotations

import pytest
from sqlalchemy import String

from app.connectors.base import NormalizedTender
from app.models import Tender
from app.seed import load_fixtures

# Column name -> declared max length, for every length-capped string column.
LIMITS: dict[str, int] = {
    column.name: column.type.length
    for column in Tender.__table__.columns
    if isinstance(column.type, String) and column.type.length
}


def overflows(tender: NormalizedTender) -> list[tuple[str, int, int]]:
    """(field, actual length, limit) for every value too long to store."""
    out = []
    for field, limit in LIMITS.items():
        value = getattr(tender, field, None)
        if isinstance(value, str) and len(value) > limit:
            out.append((field, len(value), limit))
    return out


def test_the_limits_map_is_actually_populated() -> None:
    """Guard the guard: a refactor that drops the String types must not pass."""
    assert "buyer_country" in LIMITS
    assert "source_notice_id" in LIMITS


def test_buyer_country_holds_a_full_country_name() -> None:
    """The World Bank connector emits names, not ISO codes."""
    assert LIMITS["buyer_country"] >= len("United Kingdom of Great Britain and Northern Ireland")


@pytest.mark.parametrize("tender", load_fixtures(), ids=lambda t: t.source_notice_id)
def test_every_seed_fixture_fits_the_schema(tender: NormalizedTender) -> None:
    problems = overflows(tender)
    assert not problems, (
        f"{tender.source_notice_id} cannot be stored on a length-enforcing engine: "
        + ", ".join(f"{f} is {n} chars, limit {limit}" for f, n, limit in problems)
    )


def test_a_deliberately_oversized_value_is_detected() -> None:
    """Negative control: the check must actually be able to fail."""
    tender = load_fixtures()[0]
    tender.buyer_country = "X" * (LIMITS["buyer_country"] + 1)
    assert overflows(tender) == [("buyer_country", LIMITS["buyer_country"] + 1, LIMITS["buyer_country"])]
