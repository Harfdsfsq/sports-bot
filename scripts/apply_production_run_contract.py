from __future__ import annotations

"""Final production run contract for HARIZON.

This script is intentionally small and side-effect free beyond environment files.
It writes the current intended production contract into GitHub Actions env so the
large legacy workflow cannot drift back to old B-tier/SportLogic settings.
"""

import os
from pathlib import Path

OVERRIDES = {
    # Publication coverage contract.
    "PUBLISH_TIER_A_MIN_ODDS_SOURCES": "2",
    "PUBLISH_TIER_A_MIN_BOOKS": "2",
    "PUBLISH_TIER_A_MIN_CONTEXT_SOURCES": "2",
    "PUBLISH_TIER_B_MIN_ODDS_SOURCES": "1",
    "PUBLISH_TIER_B_MIN_BOOKS": "2",
    "PUBLISH_TIER_B_MIN_CONTEXT_SOURCES": "1",
    "CONTROLLED_FALLBACK_MIN_ODDS_SOURCES": "1",
    "CONTROLLED_FALLBACK_MIN_CONTEXT_SOURCES": "1",
    "CONTROLLED_FALLBACK_MIN_CONFIRMATION_SOURCES": "1",
    "CONTROLLED_FALLBACK_TIER_B_MIN_BOOKS": "2",
    "CONTROLLED_FALLBACK_TIER_B_MIN_CONTEXT_SOURCES": "1",
    "CONTROLLED_FALLBACK_TIER_B_MIN_CONFIRMATION_SOURCES": "1",
    "CONTROLLED_FALLBACK_REQUIRE_2_ODDS_SOURCES_FOR_TELEGRAM": "false",
    "CONTROLLED_FALLBACK_REQUIRE_2_CONTEXT_SOURCES_FOR_TELEGRAM": "false",
    "CONTROLLED_FALLBACK_ALLOW_MARKET_IMPLIED_XG_FOR_B_TIER": "true",
    "CONTROLLED_FALLBACK_TIER_B_WEIGHTED_REQUIRE_XG_HARD_CONFIRMATION": "false",
    # Keep quality/value meaningful, but allow testable B-tier candidates to pass
    # when they have real edge/EV and line movement.
    "CONTROLLED_FALLBACK_BASE_TIER_B_MIN_PUBLICATION_SCORE": "12.0",
    "CONTROLLED_FALLBACK_TIER_B_MIN_PUBLICATION_SCORE": "12.0",
    "CONTROLLED_FALLBACK_TIER_B_MIN_EDGE_PP": "2.3",
    "CONTROLLED_FALLBACK_TIER_B_MIN_EV_PCT": "4.0",
    "CONTROLLED_FALLBACK_FINAL_MIN_EDGE_PP": "2.3",
    "CONTROLLED_FALLBACK_FINAL_MIN_EV_PCT": "4.0",
    # Known-bad provider path.
    "DAY_INVENTORY_ENABLE_SPORTLOGIC": "false",
    "ENABLE_SPORTLOGIC": "false",
    "SPORTLOGIC_ENABLED": "false",
    "SPORTLOGIC_CONTROLLED_ODDS_ENABLED": "false",
    "SPORTLOGIC_BROAD_FALLBACK_ENABLED": "false",
    "SPORTLOGIC_ODDS_DISCOVERY_FALLBACK_ENABLED": "false",
    "SPORTLOGIC_PER_RUN_MAX": "0",
    "SPORTLOGIC_MAX_REQUESTS_PER_RUN": "0",
    "SPORTLOGIC_MAX_HTTP_REQUESTS_PER_RUN": "0",
    "SPORTLOGIC_REQUESTS_MAX_PER_RUN": "0",
    "SPORTLOGIC_REQUEST_BUDGET_GRANTED": "0",
    "SPORTLOGIC_MATCH_LIMIT": "0",
    "SPORTLOGIC_CONTEXT_MATCH_LIMIT": "0",
    "SPORTLOGIC_ODDS_MATCH_LIMIT": "0",
    "SPORTLOGIC_DISABLED_ZERO_ROWS_GUARD": "true",
}


def main() -> int:
    for key, value in OVERRIDES.items():
        os.environ[key] = value
    env_file = os.getenv("GITHUB_ENV")
    if env_file:
        path = Path(env_file)
        with path.open("a", encoding="utf-8") as f:
            for key, value in OVERRIDES.items():
                f.write(f"{key}={value}\n")
    out = Path(".data/exports/latest-production-run-contract.env")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(f"{k}={v}" for k, v in sorted(OVERRIDES.items())) + "\n", encoding="utf-8")
    print(f"production contract applied: {len(OVERRIDES)} overrides")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
