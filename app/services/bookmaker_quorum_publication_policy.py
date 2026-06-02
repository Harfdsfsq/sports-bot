from __future__ import annotations

"""Runtime policy switch: publication price confirmation is 2+ bookmakers.

This does not fabricate odds and does not weaken price integrity. It only changes
publication coverage from "two independent API odds providers" to:

* at least one real line source/price feed for the selected market side;
* at least two distinct bookmakers in the same-side price bucket;
* existing 2+ context, xG, line-movement and price-integrity guards remain active.
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPORT = Path('.data/exports/latest-bookmaker-quorum-publication-policy.json')


def _set_default(key: str, value: str) -> None:
    if os.getenv(key) in (None, ''):
        os.environ[key] = value


def _write(payload: dict[str, Any]) -> None:
    try:
        REPORT.parent.mkdir(parents=True, exist_ok=True)
        REPORT.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    except Exception:
        pass


def install() -> dict[str, Any]:
    defaults = {
        'PUBLISH_PRICE_CONFIRMATION_MODE': 'bookmakers',
        'PUBLISH_MIN_ODDS_SOURCES': '1',
        'TELEGRAM_MIN_ODDS_SOURCES': '1',
        'MIN_SOURCES_PUBLISH': '1',
        'PUBLISH_MIN_BOOKS': '2',
        'MIN_BOOKS_PUBLISH': '2',
        'CORE_COVERAGE_MIN_ODDS_SOURCES': '1',
        'CORE_COVERAGE_MIN_BOOKMAKERS': '2',
        'CONTROLLED_FALLBACK_MIN_ODDS_SOURCES': '1',
        'CONTROLLED_FALLBACK_TIER_A_MIN_ODDS_SOURCES': '1',
        'CONTROLLED_FALLBACK_TIER_B_MIN_ODDS_SOURCES': '1',
        'CONTROLLED_FALLBACK_TIER_C_MIN_ODDS_SOURCES': '1',
        'CONTROLLED_FALLBACK_TIER_A_MIN_BOOKS': '2',
        'CONTROLLED_FALLBACK_TIER_B_MIN_BOOKS': '2',
        'CONTROLLED_FALLBACK_TIER_C_MIN_BOOKS': '2',
        'CONTROLLED_FALLBACK_REQUIRE_2_BOOKS_FOR_TELEGRAM': 'true',
        'CONTROLLED_FALLBACK_REQUIRE_2_ODDS_SOURCES_FOR_TELEGRAM': 'false',
        'CONTROLLED_FALLBACK_REQUIRE_BOOKMAKER_QUORUM_FOR_TELEGRAM': 'true',
    }
    for key, value in defaults.items():
        _set_default(key, value)

    payload = {
        'created_at_utc': datetime.now(timezone.utc).isoformat(),
        'status': 'installed',
        'policy': '2plus_bookmakers_not_2plus_odds_sources',
        'min_odds_sources': int(float(os.getenv('PUBLISH_MIN_ODDS_SOURCES') or 1)),
        'min_bookmakers': int(float(os.getenv('PUBLISH_MIN_BOOKS') or os.getenv('MIN_BOOKS_PUBLISH') or 2)),
        'fallback_min_odds_sources': int(float(os.getenv('CONTROLLED_FALLBACK_MIN_ODDS_SOURCES') or 1)),
        'fallback_require_2_odds_sources': str(os.getenv('CONTROLLED_FALLBACK_REQUIRE_2_ODDS_SOURCES_FOR_TELEGRAM') or '').lower(),
        'price_integrity_guard_enabled': str(os.getenv('CONTROLLED_FALLBACK_PRICE_INTEGRITY_GUARD_ENABLED') or '').lower(),
    }
    _write(payload)
    return payload
