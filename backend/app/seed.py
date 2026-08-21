"""Load fixture tenders for UI testing: ``python -m app.seed [--reset]``.

Seed records are clearly marked (SEED-* notice ids). Connectors never emit
sample data - this command is the only path that inserts fixtures.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import delete

from app.connectors.base import NormalizedTender
from app.db import SessionLocal, init_db
from app.logging_config import configure_logging
from app.models import Tender
from app.services.ingest import store_tenders

FIXTURE = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "seed_tenders.json"


def load_fixtures(path: Path = FIXTURE, now: datetime | None = None) -> list[NormalizedTender]:
    now = now or datetime.now(UTC).replace(tzinfo=None)
    records = json.loads(path.read_text(encoding="utf-8"))
    out: list[NormalizedTender] = []
    for record in records:
        data = dict(record)
        published = data.pop("publication_offset_days", None)
        deadline = data.pop("deadline_offset_days", None)
        if published is not None:
            data["publication_date"] = now + timedelta(days=published)
        if deadline is not None:
            data["deadline"] = now + timedelta(days=deadline)
        data["source_updated_at"] = data.get("publication_date")
        data.setdefault("source_url", data.get("document_urls") or [None])
        if isinstance(data["source_url"], list):
            data["source_url"] = data["source_url"][0] if data["source_url"] else None
        data["raw_payload"] = {"seed_fixture": True, **record}
        out.append(NormalizedTender(**data))
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed fixture tenders for UI testing")
    parser.add_argument("--reset", action="store_true", help="delete existing SEED-* rows first")
    args = parser.parse_args()

    configure_logging("INFO")
    init_db()
    tenders = load_fixtures()
    db = SessionLocal()
    try:
        if args.reset:
            db.execute(delete(Tender).where(Tender.source_notice_id.like("SEED-%")))
            db.commit()
        stats = store_tenders(db, tenders)
    finally:
        db.close()
    print(
        f"seeded {len(tenders)} fixture tenders: "
        f"{stats.created} created, {stats.updated} updated, {stats.unchanged} unchanged, {stats.failed} failed"
    )


if __name__ == "__main__":
    main()
