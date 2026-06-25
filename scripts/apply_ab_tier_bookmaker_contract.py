from __future__ import annotations

"""Apply the HARIZON A-tier-only public publication evidence contract.

A-tier is the only public Telegram publication tier: 2 odds/line sources,
2 bookmaker price confirmations and 2 context sources. B-tier is kept as a
watchlist-only lifecycle state and promotion seed.
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path('.').resolve()
OUT = ROOT / '.data' / 'exports' / 'latest-ab-tier-bookmaker-contract-policy.json'
B_WATCH_ONLY_SENTINEL = '__b_tier_watch_only_no_publication__'

CONTRACT_ENV = {
    'PUBLISH_ALLOW_B_TIER': 'false',
    'PUBLISH_B_TIER_WATCH_ONLY': 'true',
    'PUBLISH_COVERAGE_TIER_MODE': 'a_only_publish_b_watchlist',
    'PUBLISH_MIN_BOOKS': '2',
    'MIN_BOOKS_PUBLISH': '2',
    'PUBLISH_MIN_CONTEXT_SOURCES': '2',
    'MIN_CONTEXT_SOURCES_PUBLISH': '2',
    'PUBLISH_MIN_ODDS_SOURCES': '2',
    'MIN_SOURCES_PUBLISH': '2',
    'PUBLISH_TIER_A_MIN_ODDS_SOURCES': '2',
    'PUBLISH_TIER_A_MIN_BOOKS': '2',
    'PUBLISH_TIER_A_MIN_CONTEXT_SOURCES': '2',
    # B-tier remains visible as watchlist-only.
    'PUBLISH_TIER_B_MIN_ODDS_SOURCES': '1',
    'PUBLISH_TIER_B_MIN_BOOKS': '2',
    'PUBLISH_TIER_B_MIN_CONTEXT_SOURCES': '1',
    'CONTROLLED_FALLBACK_TIER_B_ALLOWED_FAMILIES': B_WATCH_ONLY_SENTINEL,
    'CONTROLLED_FALLBACK_TELEGRAM_ALLOW_TIER_B': 'false',
    'CONTROLLED_FALLBACK_TIER_B_WATCH_ONLY': 'true',
    'CONTROLLED_FALLBACK_TIER_B_PUBLISH_ENABLED': 'false',
    'CONTROLLED_FALLBACK_MIN_ODDS_SOURCES': '2',
    'CONTROLLED_FALLBACK_MIN_CONTEXT_SOURCES': '2',
    'CONTROLLED_FALLBACK_MIN_CONFIRMATION_SOURCES': '2',
    'CONTROLLED_FALLBACK_REQUIRE_2_BOOKS_FOR_TELEGRAM': 'true',
    'CONTROLLED_FALLBACK_REQUIRE_2_ODDS_SOURCES_FOR_TELEGRAM': 'true',
    'CONTROLLED_FALLBACK_REQUIRE_2_CONTEXT_SOURCES_FOR_TELEGRAM': 'true',
    'CONTROLLED_FALLBACK_REQUIRE_INDEPENDENT_SOURCES': 'true',
    'CONTROLLED_FALLBACK_TIER_A_REQUIRE_2_ODDS_SOURCES': 'true',
    'CONTROLLED_FALLBACK_TIER_A_MIN_ODDS_SOURCES': '2',
    'CONTROLLED_FALLBACK_TIER_A_MIN_BOOKS': '2',
    'CONTROLLED_FALLBACK_TIER_A_MIN_CONTEXT_SOURCES': '2',
    'CONTROLLED_FALLBACK_TIER_B_REQUIRE_INDEPENDENT_SOURCES': 'true',
    'CONTROLLED_FALLBACK_TIER_B_MIN_ODDS_SOURCES': '1',
    'CONTROLLED_FALLBACK_TIER_B_MIN_BOOKS': '2',
    'CONTROLLED_FALLBACK_TIER_B_MIN_BOOKMAKERS': '2',
    'CONTROLLED_FALLBACK_TIER_B_MIN_CONTEXT_SOURCES': '1',
    'CONTROLLED_FALLBACK_TIER_B_MIN_CONFIRMATION_SOURCES': '1',
}


def _append_github_env(values: dict[str, str]) -> None:
    os.environ.update(values)
    path = os.getenv('GITHUB_ENV')
    if not path:
        for key in sorted(values):
            print(f'{key}={values[key]}')
        return
    with open(path, 'a', encoding='utf-8') as fh:
        for key in sorted(values):
            fh.write(f'{key}={values[key]}\n')


def main() -> int:
    _append_github_env(CONTRACT_ENV)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        'created_at_utc': datetime.now(timezone.utc).isoformat(),
        'status': 'applied',
        'contract': {
            'public_publication_tier': 'A-only',
            'A': {'min_odds_sources': 2, 'min_bookmakers': 2, 'min_context_sources': 2},
            'B': {'mode': 'watchlist_only', 'min_odds_sources': 1, 'min_bookmakers': 2, 'min_context_sources': 1},
            'guards_unchanged': ['value', 'xg', 'quality_score', 'line_movement', 'price_integrity', 'dedupe', 'daily_limit', 'publish_window'],
        },
        'env': CONTRACT_ENV,
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    print('Applied HARIZON A-only publication contract: A=2 odds/2 books/2 context; B=watchlist-only.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
