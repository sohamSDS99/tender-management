"""Frozen-core guard: the relevance engine's verdict on the 14 seed fixtures.

The engine, its config and the seed fixtures are all frozen. This test pins their
combined output to a single hash so any accidental change to scoring - a reworded
reason, a shifted band, an edited profile - fails loudly instead of quietly
changing what gets announced to Slack.

`now` is pinned because several signals (deadline proximity, is_actionable)
depend on it. Regenerate the hash *only* with a deliberate, reviewed change to
the relevance engine:

    python -m tests.test_relevance_baseline
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime

from app.seed import load_fixtures
from app.services.relevance import get_engine

# Captured from the untouched baseline (97 tests green, before any deployment work).
BASELINE_SHA256 = "fb3ff8e6ba65e1f21cfa51381f9b6959e5f724f025b3c9e03a8ded734de2c17d"
PINNED_NOW = datetime(2026, 1, 15, 12, 0, 0)
FIXTURE_COUNT = 14


def canonical_scores(now: datetime = PINNED_NOW) -> str:
    engine = get_engine()
    rows = []
    for tender in load_fixtures(now=now):
        result = engine.score_obj(tender, now=now)
        rows.append(
            {
                "source": tender.source,
                "notice": tender.source_notice_id,
                **result.as_dict(),
                "relevance_category_label": result.relevance_category_label,
                "profile_scores": result.profile_scores,
            }
        )
    rows.sort(key=lambda r: (r["source"], r["notice"]))
    return json.dumps(rows, indent=2, sort_keys=True, default=str, ensure_ascii=False)


def test_seed_fixture_count_is_unchanged() -> None:
    assert len(load_fixtures(now=PINNED_NOW)) == FIXTURE_COUNT


def test_relevance_output_is_byte_identical_to_baseline() -> None:
    blob = canonical_scores()
    digest = hashlib.sha256(blob.encode("utf-8")).hexdigest()
    assert digest == BASELINE_SHA256, (
        "The relevance engine's output for the seed fixtures changed.\n"
        f"expected {BASELINE_SHA256}\n"
        f"actual   {digest}\n"
        "config/relevance_profiles.yaml, app/services/relevance.py and the seed "
        "fixtures are frozen. If this change is deliberate, regenerate the hash "
        "with: python -m tests.test_relevance_baseline"
    )


def test_every_score_band_is_still_represented() -> None:
    """The fixtures must keep spanning the bands the Slack threshold sits inside."""
    rows = json.loads(canonical_scores())
    scores = [r["relevance_score"] for r in rows]
    assert max(scores) >= 85, "no excellent-fit fixture left"
    assert any(70 <= s for s in scores), "nothing clears the default SLACK_MIN_SCORE"
    assert any(s < 25 for s in scores), "no clear false-positive fixture left"
    assert any(r["disqualifiers"] for r in rows), "no disqualifier case left"


if __name__ == "__main__":  # regeneration helper
    blob = canonical_scores()
    print(blob)
    print("SHA256:", hashlib.sha256(blob.encode("utf-8")).hexdigest())
