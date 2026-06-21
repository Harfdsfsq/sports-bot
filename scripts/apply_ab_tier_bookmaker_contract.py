from __future__ import annotations

"""Apply the HARIZON A/B publication evidence contract.

Owner contract:
- A-tier: 2 odds/line confirmations, 2 bookmakers, 2 contexts.
- B-tier: 1 odds/line source, 1 bookmaker, 1 context.

The normal value/quality/price-integrity/line-movement/dedupe guards stay active.
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path

CONTRACT_ENV = {
    # Core A/B coverage contract.
    "PUBLISH_ALLOW_B_TIER": "true",
    "PUBLISH_COVERAGE_TIER_MODE": "hybrid",
    "PUBLISH_MIN_BOOKS": "1",
    "MIN_BOOKS_PUBLISH": "1",
    "PUBLISH_MIN_CONTEXT_SOURCES": "1",
    "MIN_CONTEXT_SOURCES_PUBLISH": "1",
    "PUBLISH_MIN_ODDS_SOURCES": "1",
    "MIN_SOURCES_PUBLISH": "1",
    "PUBLISH_TIER_A_MIN_ODDS_SOURCES": "2",
    "PUBLISH_TIER_A_MIN_BOOKS": "2",
    "PUBLISH_TIER_A_MIN_CONTEXT_SOURCES": "2",
    "PUBLISH_TIER_B_MIN_ODDS_SOURCES": "1",
    "PUBLISH_TIER_B_MIN_BOOKS": "1",
    "PUBLISH_TIER_B_MIN_CONTEXT_SOURCES": "1",

    # Controlled fallback follows the same owner contract.
    "CONTROLLED_FALLBACK_MIN_ODDS_SOURCES": "1",
    "CONTROLLED_FALLBACK_MIN_CONTEXT_SOURCES": "1",
    "CONTROLLED_FALLBACK_MIN_CONFIRMATION_SOURCES": "1",
    "CONTROLLED_FALLBACK_REQUIRE_2_BOOKS_FOR_TELEGRAM": "false",
    "CONTROLLED_FALLBACK_REQUIRE_2_ODDS_SOURCES_FOR_TELEGRAM": "false",
    "CONTROLLED_FALLBACK_REQUIRE_2_CONTEXT_SOURCES_FOR_TELEGRAM": "false",
    "CONTROLLED_FALLBACK_REQUIRE_INDEPENDENT_SOURCES": "false",
    "CONTROLLED_FALLBACK_TIER_A_REQUIRE_2_ODDS_SOURCES": "true",
    "CONTROLLED_FALLBACK_TIER_A_MIN_ODDS_SOURCES": "2",
    "CONTROLLED_FALLBACK_TIER_A_MIN_BOOKS": "2",
    "CONTROLLED_FALLBACK_TIER_A_MIN_CONTEXT_SOURCES": "2",
    "CONTROLLED_FALLBACK_TIER_B_REQUIRE_ODDS_SOURCES": "true",
    "CONTROLLED_FALLBACK_TIER_B_REQUIRE_INDEPENDENT_SOURCES": "false",
    "CONTROLLED_FALLBACK_TIER_B_MIN_ODDS_SOURCES": "1",
    "CONTROLLED_FALLBACK_TIER_B_MIN_BOOKS": "1",
    "CONTROLLED_FALLBACK_TIER_B_MIN_CONTEXT_SOURCES": "1",
    "CONTROLLED_FALLBACK_TIER_B_MIN_CONFIRMATION_SOURCES": "1",
    "CONTROLLED_FALLBACK_TIER_B_BOOKMAKER_QUORUM_PRICE_GUARD": "true",

    # Best-of-day allocator: do not spend all 3 B-tier fallback slots too early.
    # Before 18:00 MSK only 2 normal fallback slots are available; the reserved
    # slot can still be used by an elite candidate. After 18:00 all slots unlock.
    "CONTROLLED_FALLBACK_RESERVED_DAILY_SLOT_ENABLED": "true",
    "CONTROLLED_FALLBACK_RESERVED_DAILY_SLOTS": "1",
    "CONTROLLED_FALLBACK_RESERVED_SLOT_RELEASE_LOCAL_HOUR": "18",
    "CONTROLLED_FALLBACK_RESERVED_SLOT_RELEASE_LOCAL_MINUTE": "0",
    "CONTROLLED_FALLBACK_RESERVED_SLOT_ELITE_MIN_EV_PCT": "12.0",
    "CONTROLLED_FALLBACK_RESERVED_SLOT_ELITE_MIN_EDGE_PP": "6.5",
    "CONTROLLED_FALLBACK_RESERVED_SLOT_ELITE_MIN_CONFIDENCE": "73.0",
    "CONTROLLED_FALLBACK_RESERVED_SLOT_ELITE_MIN_QUALITY": "74.0",

    # V7: prevent unpublished/evaluated rows from blocking future review as
    # duplicate_match_market_selection_line. Published/sent/state indexes remain.
    "CONTROLLED_FALLBACK_STRICT_MATCH_MARKET_DEDUPE": "false",
    "CONTROLLED_FALLBACK_DEDUPE_SENT_INDEX_STRICT": "true",
    "CONTROLLED_FALLBACK_DEDUPE_PREVIOUS_REPORT": "true",

    # Keep publication quality strict enough for reserve mode.
    "CONTROLLED_FALLBACK_REQUIRE_TOTALS_SANITY_FOR_TELEGRAM": "true",
    "CONTROLLED_FALLBACK_TIER_B_MIN_CONFIDENCE": "68.0",
    "CONTROLLED_FALLBACK_TIER_B_MIN_QUALITY": "70.0",
    "CONTROLLED_FALLBACK_TIER_B_MIN_EDGE_PP": "5.0",
    "CONTROLLED_FALLBACK_TIER_B_MIN_EV_PCT": "8.0",
    "CONTROLLED_FALLBACK_TIER_B_MIN_PUBLICATION_SCORE": "20.0",
    "CONTROLLED_FALLBACK_FINAL_MIN_EDGE_PP": "5.0",
    "CONTROLLED_FALLBACK_FINAL_MIN_EV_PCT": "8.0",
    "CONTROLLED_FALLBACK_PROXY_SINGLE_SOURCE_STRICT": "true",
    "CONTROLLED_FALLBACK_PROXY_SINGLE_SOURCE_MIN_EDGE_PP": "5.0",
    "CONTROLLED_FALLBACK_PROXY_SINGLE_SOURCE_MIN_EV_PCT": "8.0",
    "CONTROLLED_FALLBACK_PROXY_SINGLE_SOURCE_MIN_CONFIDENCE": "70.0",

    # Keep promotion focused on candidates that could realistically survive the
    # final B-tier value bar, while still producing useful diagnostics.
    "PROMOTE_B_COVER_MIN_EDGE_PP": "2.0",
    "PROMOTE_B_COVER_MIN_EV_PCT": "4.0",
    "PROMOTE_B_COVER_VALUE_CANDIDATE_LIMIT": "36",
}


def _append_github_env(values: dict[str, str]) -> None:
    path = os.getenv("GITHUB_ENV")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as fh:
        for key, value in values.items():
            fh.write(f"{key}={value}\n")


def main() -> int:
    os.environ.update(CONTRACT_ENV)
    _append_github_env(CONTRACT_ENV)
    out = Path(".data/exports/latest-ab-tier-bookmaker-contract-policy.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "applied",
        "contract": {
            "A": {"min_odds_sources": 2, "min_bookmakers": 2, "min_context_sources": 2},
            "B": {"min_odds_sources": 1, "min_bookmakers": 1, "min_context_sources": 1},
            "daily_allocator": {
                "reserved_slots": 1,
                "release_local_time": "18:00",
                "elite_override": {"min_ev_pct": 12.0, "min_edge_pp": 6.5, "min_confidence": 73.0, "min_quality": 74.0},
            },
            "independent_odds_sources": "required_for_a_tier_only",
            "guards_unchanged": ["quality", "line_movement", "price_integrity", "dedupe", "daily_limit", "publish_window"],
        },
        "env": CONTRACT_ENV,
    }
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print("Applied HARIZON A/B publication contract: A=2 odds/2 books/2 context, B=1 odds/1 book/1 context; reserved daily slot enabled")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
