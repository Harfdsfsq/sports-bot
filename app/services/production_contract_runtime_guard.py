from __future__ import annotations

"""Last-writer runtime guard for the production HARIZON run.

Several legacy startup patches and workflow variables can re-enable old settings
after the intended contract has been applied. This module is installed at the end
of runtime_startup_chain and force-writes only the current safety contract:

* A-tier remains strict: 2 odds sources / 2 books / 2 context sources.
* B-tier is testable but still guarded: 1 odds source / 2 books / 1 context.
* SportLogic is disabled while the endpoint returns zero useful rows.
* No value, price-integrity, movement, quality, dedupe or market-family guards
  are disabled here.
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

OUT = Path(".data/exports/latest-production-contract-runtime-guard.json")

OVERRIDES: dict[str, str] = {
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
    # B-tier may use market-implied xG as sanity, but not as A-tier hard xG.
    "CONTROLLED_FALLBACK_ALLOW_MARKET_IMPLIED_XG_FOR_B_TIER": "true",
    "CONTROLLED_FALLBACK_TIER_B_WEIGHTED_REQUIRE_XG_HARD_CONFIRMATION": "false",
    "CONTROLLED_FALLBACK_BLOCK_PROXY_DEFAULT_XG_ALL_TIERS": "true",
    "CONTROLLED_FALLBACK_B_TIER_REQUIRE_HARD_CONTEXT": "false",
    "CONTROLLED_FALLBACK_B_TIER_BLOCK_LOW_QUALITY_COMPETITIONS": "true",
    # Keep the B-tier testable while retaining EV/edge floors.
    "CONTROLLED_FALLBACK_BASE_TIER_B_MIN_PUBLICATION_SCORE": "12.0",
    "CONTROLLED_FALLBACK_TIER_B_MIN_PUBLICATION_SCORE": "12.0",
    "CONTROLLED_FALLBACK_TIER_B_MIN_EDGE_PP": "2.3",
    "CONTROLLED_FALLBACK_TIER_B_MIN_EV_PCT": "4.0",
    "CONTROLLED_FALLBACK_FINAL_MIN_EDGE_PP": "2.3",
    "CONTROLLED_FALLBACK_FINAL_MIN_EV_PCT": "4.0",
    # Known-bad provider path from recent production reports: 30 req / 0 rows.
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
    "SPORTLOGIC_ODDS_DISCOVERY_MAX_PAGES": "0",
    "SPORTLOGIC_ODDS_DISCOVERY_GAME_DETAIL_LIMIT": "0",
    "SPORTLOGIC_DISABLED_ZERO_ROWS_GUARD": "true",
}


def _write_report(previous: dict[str, Any]) -> None:
    try:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(
            json.dumps(
                {
                    "status": "installed",
                    "created_at_utc": datetime.now(timezone.utc).isoformat(),
                    "overrides": OVERRIDES,
                    "previous": previous,
                    "publication_contract_relaxed": False,
                    "sportlogic_disabled_zero_rows_guard": True,
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
    except Exception:
        pass


def install() -> dict[str, Any]:
    previous = {key: os.getenv(key) for key in OVERRIDES}
    for key, value in OVERRIDES.items():
        os.environ[key] = value
    _write_report(previous)
    return {
        "status": "installed",
        "overrides_count": len(OVERRIDES),
        "publication_contract_relaxed": False,
        "sportlogic_disabled_zero_rows_guard": True,
    }


__all__ = ["install", "OVERRIDES"]
