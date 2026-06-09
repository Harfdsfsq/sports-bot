from __future__ import annotations

"""Apply HARIZON A/B tier bookmaker-context publication contract.

Owner contract:
- B-tier: 1+ bookmaker/price confirmation and 1+ context source;
- A-tier: 2+ bookmakers/price confirmations and 2+ context sources.

This script writes the contract into GitHub Actions env and records a small
runtime artifact.  It intentionally does not relax value, xG, movement or
price-integrity guards.
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path

CONTRACT_ENV = {
    "PUBLISH_ALLOW_B_TIER": "true",
    "PUBLISH_COVERAGE_TIER_MODE": "hybrid",
    "PUBLISH_MIN_BOOKS": "1",
    "MIN_BOOKS_PUBLISH": "1",
    "PUBLISH_MIN_CONTEXT_SOURCES": "1",
    "MIN_CONTEXT_SOURCES_PUBLISH": "1",
    "PUBLISH_MIN_ODDS_SOURCES": "1",
    "MIN_SOURCES_PUBLISH": "1",
    "PUBLISH_TIER_A_MIN_BOOKS": "2",
    "PUBLISH_TIER_A_MIN_CONTEXT_SOURCES": "2",
    "PUBLISH_TIER_B_MIN_BOOKS": "1",
    "PUBLISH_TIER_B_MIN_CONTEXT_SOURCES": "1",
    "CONTROLLED_FALLBACK_MIN_ODDS_SOURCES": "1",
    "CONTROLLED_FALLBACK_REQUIRE_2_ODDS_SOURCES_FOR_TELEGRAM": "false",
    "CONTROLLED_FALLBACK_TIER_A_REQUIRE_2_ODDS_SOURCES": "false",
    "CONTROLLED_FALLBACK_TIER_A_MIN_BOOKS": "2",
    "CONTROLLED_FALLBACK_TIER_A_MIN_CONTEXT_SOURCES": "2",
    "CONTROLLED_FALLBACK_TIER_B_MIN_BOOKS": "1",
    "CONTROLLED_FALLBACK_TIER_B_MIN_CONTEXT_SOURCES": "1",
    "CONTROLLED_FALLBACK_TIER_B_BOOKMAKER_QUORUM_PRICE_GUARD": "true",
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
            "A": {"min_bookmakers": 2, "min_context_sources": 2},
            "B": {"min_bookmakers": 1, "min_context_sources": 1},
            "independent_odds_sources": "diagnostic_only",
            "guards_unchanged": ["value", "xg", "quality", "line_movement", "price_integrity"],
        },
        "env": CONTRACT_ENV,
    }
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print("Applied A/B tier bookmaker contract: B=1+bookmaker+1+context; A=2+bookmakers+2+contexts")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
