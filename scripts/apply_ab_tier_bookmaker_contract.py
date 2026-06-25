from __future__ import annotations

"""Apply the HARIZON A/B public publication evidence contract.

A-tier stays strict public: 2 odds/line sources, 2 bookmaker price confirmations
and 2 context sources.  B-tier is a controlled public fallback tier: 1 odds/line
source, 2 same-market bookmaker price confirmations and 1 context source.

The script does not relax final publication guards.  Value, xG/market sanity,
quality score, line movement, price integrity, duplicate, daily limit and publish
window checks remain mandatory before Telegram publication.
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path('.').resolve()
OUT = ROOT / '.data' / 'exports' / 'latest-ab-tier-bookmaker-contract-policy.json'

CONTRACT_ENV = {
    'PUBLISH_ALLOW_B_TIER': 'true',
    'PUBLISH_B_TIER_WATCH_ONLY': 'false',
    'PUBLISH_COVERAGE_TIER_MODE': 'hybrid_public_a_controlled_b',
    'PUBLISH_MIN_BOOKS': '1',
    'MIN_BOOKS_PUBLISH': '1',
    'PUBLISH_MIN_CONTEXT_SOURCES': '1',
    'MIN_CONTEXT_SOURCES_PUBLISH': '1',
    'PUBLISH_MIN_ODDS_SOURCES': '1',
    'MIN_SOURCES_PUBLISH': '1',
    'PUBLISH_TIER_A_MIN_ODDS_SOURCES': '2',
    'PUBLISH_TIER_A_MIN_BOOKS': '2',
    'PUBLISH_TIER_A_MIN_CONTEXT_SOURCES': '2',
    'PUBLISH_TIER_B_MIN_ODDS_SOURCES': '1',
    'PUBLISH_TIER_B_MIN_BOOKS': '2',
    'PUBLISH_TIER_B_MIN_CONTEXT_SOURCES': '1',
    'CONTROLLED_FALLBACK_TIER_B_ALLOWED_FAMILIES': 'totals,spreads',
    'CONTROLLED_FALLBACK_TELEGRAM_ALLOW_TIER_B': 'true',
    'CONTROLLED_FALLBACK_TIER_B_WATCH_ONLY': 'false',
    'CONTROLLED_FALLBACK_TIER_B_PUBLISH_ENABLED': 'true',
    'CONTROLLED_FALLBACK_MIN_ODDS_SOURCES': '1',
    'CONTROLLED_FALLBACK_MIN_CONTEXT_SOURCES': '1',
    'CONTROLLED_FALLBACK_MIN_CONFIRMATION_SOURCES': '1',
    'CONTROLLED_FALLBACK_REQUIRE_2_BOOKS_FOR_TELEGRAM': 'false',
    'CONTROLLED_FALLBACK_REQUIRE_2_ODDS_SOURCES_FOR_TELEGRAM': 'false',
    'CONTROLLED_FALLBACK_REQUIRE_2_CONTEXT_SOURCES_FOR_TELEGRAM': 'false',
    'CONTROLLED_FALLBACK_REQUIRE_INDEPENDENT_SOURCES': 'false',
    'CONTROLLED_FALLBACK_TIER_A_REQUIRE_2_ODDS_SOURCES': 'true',
    'CONTROLLED_FALLBACK_TIER_A_MIN_ODDS_SOURCES': '2',
    'CONTROLLED_FALLBACK_TIER_A_MIN_BOOKS': '2',
    'CONTROLLED_FALLBACK_TIER_A_MIN_CONTEXT_SOURCES': '2',
    'CONTROLLED_FALLBACK_TIER_A_MIN_CONFIRMATION_SOURCES': '2',
    'CONTROLLED_FALLBACK_TIER_B_REQUIRE_INDEPENDENT_SOURCES': 'false',
    'CONTROLLED_FALLBACK_TIER_B_REQUIRE_ODDS_SOURCES': 'true',
    'CONTROLLED_FALLBACK_TIER_B_MIN_ODDS_SOURCES': '1',
    'CONTROLLED_FALLBACK_TIER_B_MIN_BOOKS': '2',
    'CONTROLLED_FALLBACK_TIER_B_MIN_BOOKMAKERS': '2',
    'CONTROLLED_FALLBACK_TIER_B_MIN_CONTEXT_SOURCES': '1',
    'CONTROLLED_FALLBACK_TIER_B_MIN_CONFIRMATION_SOURCES': '1',
    'CONTROLLED_FALLBACK_TIER_B_REQUIRE_2_BOOKS_FOR_TELEGRAM': 'true',
    'CONTROLLED_FALLBACK_TIER_B_BOOKMAKER_QUORUM_PRICE_GUARD': 'true',
    # Materialize A-cover candidates before the final 2h publish window so the
    # line-movement lifecycle can stage them for the next cron instead of finding
    # them too late.
    'PROMOTE_A_COVER_ONLY_PUBLISH_WINDOW': 'false',
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
            'public_publication_tier': 'A-public plus controlled-B-public-fallback',
            'A': {'min_odds_sources': 2, 'min_bookmakers': 2, 'min_context_sources': 2},
            'B': {
                'mode': 'controlled_public_fallback',
                'min_odds_sources': 1,
                'min_bookmakers': 2,
                'min_context_sources': 1,
                'same_market_bookmaker_quorum': True,
            },
            'guards_unchanged': ['value', 'xg', 'quality_score', 'line_movement', 'price_integrity', 'dedupe', 'daily_limit', 'publish_window'],
            'notes': [
                'B-tier is not auto-publish; it still must pass guarded fallback final checks.',
                'Quarter totals remain blocked by publication point guard.',
                'A-cover promotion may happen before the 2h publish window only to stage line-movement lifecycle candidates.',
            ],
        },
        'env': CONTRACT_ENV,
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    print('Applied HARIZON hybrid A/B publication contract: A=2/2/2 public; B=1 odds/2 books/1 context controlled public fallback.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
