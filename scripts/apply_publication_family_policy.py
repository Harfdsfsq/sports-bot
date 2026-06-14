from __future__ import annotations

"""Apply the HARIZON publication-market family and runtime signal contract.

The bot may analyze all supported markets, but public Telegram picks are limited
strictly to totals and spreads/handicaps. This script is intentionally called at
multiple workflow points, so it also pins the lightweight runtime switches that
must not be overwritten by older policy/budget layers.
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path('.').resolve()
OUT_PATH = ROOT / '.data' / 'exports' / 'latest-publication-family-policy.json'
ALLOWED = 'totals,spreads'
RUNTIME_POLICY_VERSION = 'harizon-runtime-policy-v12-public-total-line-contract'

ENV_UPDATES = {
    'HARIZON_RUNTIME_POLICY_VERSION': RUNTIME_POLICY_VERSION,
    'HARIZON_EFFECTIVE_RUNTIME_POLICY_VERSION': RUNTIME_POLICY_VERSION,
    'MARKET_FAMILY_PUBLICATION_GUARD_ENABLED': 'true',
    'PUBLICATION_ALLOWED_MARKET_FAMILIES': ALLOWED,
    'HARIZON_ALLOWED_PUBLICATION_FAMILIES': ALLOWED,
    'MAIN_PUBLISH_ALLOWED_FAMILIES': ALLOWED,
    'TELEGRAM_ALLOWED_MARKET_FAMILIES': ALLOWED,
    'CONTROLLED_FALLBACK_ALLOWED_FAMILIES': ALLOWED,
    'CONTROLLED_FALLBACK_TIER_A_ALLOWED_FAMILIES': ALLOWED,
    'CONTROLLED_FALLBACK_TIER_B_ALLOWED_FAMILIES': ALLOWED,
    'CONTROLLED_RESCUE_ALLOWED_FAMILIES': ALLOWED,
    'POST_INTEGRITY_RESCUE_ALLOWED_FAMILIES': ALLOWED,
    'QUALITY_FIRST_ALLOWED_PUBLICATION_FAMILIES': ALLOWED,
    'CANDIDATE_FACTORY_ALLOWED_PUBLICATION_FAMILIES': ALLOWED,
    'H2H_PUBLICATION_ENABLED': 'false',
    'BTTS_PUBLICATION_ENABLED': 'false',
    'DNB_PUBLICATION_ENABLED': 'false',
    'DOUBLE_CHANCE_PUBLICATION_ENABLED': 'false',
    'TEAM_TOTALS_PUBLICATION_ENABLED': 'false',
    'TOTALS_PUBLICATION_ENABLED': 'true',
    'SPREADS_PUBLICATION_ENABLED': 'true',
    # Public totals contract: publish only whole and .5 total lines. Quarter
    # totals remain analysis/watch-only and are rejected before xG sanity.
    'CONTROLLED_FALLBACK_REQUIRE_PUBLIC_TOTAL_LINE': 'true',
    'CONTROLLED_FALLBACK_REQUIRE_TOTAL_POINT_FOR_PUBLICATION': 'true',
    'PROMOTE_B_COVER_FILTER_BY_TIME': 'true',
    # Analyze broadly, publish narrowly.
    'ANALYSIS_ALLOWED_MARKET_FAMILIES': 'h2h,totals,spreads,btts,dnb,doubleChance,teamTotals',
    # Runtime signal-stack switches.
    'ODDS_MOVEMENT_SNAPSHOTS_ENABLED': 'true',
    'NEWS_INJURY_SHORTLIST_ENABLED': 'true',
    'NEWS_INJURY_SHORTLIST_LIMIT': os.getenv('NEWS_INJURY_SHORTLIST_LIMIT') or '8',
    'BZZOIRO_CURRENT_ODDS_AS_SECONDARY_SOURCE': 'true',
    'BZZOIRO_FORCE_PREDICTIONS_V2': 'true',
    'BZZOIRO_PROVIDER_SMOKE_ENABLED': 'false',
    'BZZOIRO_API_ROOT_URL': os.getenv('BZZOIRO_API_ROOT_URL') or 'https://sports.bzzoiro.com/api',
    'BZZOIRO_BASE_URL': os.getenv('BZZOIRO_BASE_URL') or 'https://sports.bzzoiro.com/api/v2',
    'BZZOIRO_PREDICTIONS_MAX_PAGES': os.getenv('BZZOIRO_PREDICTIONS_MAX_PAGES') or '8',
    'SSTATS_ROLLING_METRICS_ENABLED': 'true',
    'SSTATS_HISTORICAL_ODDS_AS_LINES': 'false',
    'CONTEXT_ENRICHMENT_REQUIRES_OFFERS': 'false',
    # Try to actually reach the documented 300-match inventory during run-once.
    'DAY_INVENTORY_TARGET_SIZE': '300',
    'DAY_INVENTORY_MAX_MATCHES': '300',
    'DAY_INVENTORY_ODDS_API_IO_TARGET_MATCHES': '300',
    'DAY_INVENTORY_MULTI_SOURCE_MAX_MATCHES': '300',
    'MAX_MATCHES_FOR_ODDS_FETCH': '300',
    # Strict publication contract from the project rules and provider document.
    'PUBLISH_ALLOW_B_TIER': 'true',
    'PUBLISH_COVERAGE_TIER_MODE': 'hybrid',
    'MIN_BOOKS_PUBLISH': '2',
    'PUBLISH_MIN_BOOKS': '2',
    'MIN_SOURCES_PUBLISH': '2',
    'PUBLISH_MIN_ODDS_SOURCES': '2',
    'MIN_CONTEXT_SOURCES_PUBLISH': '2',
    'PUBLISH_MIN_CONTEXT_SOURCES': '2',
    'PUBLISH_TIER_A_MIN_ODDS_SOURCES': '2',
    'PUBLISH_TIER_A_MIN_BOOKS': '2',
    'PUBLISH_TIER_A_MIN_CONTEXT_SOURCES': '2',
    'PUBLISH_TIER_B_MIN_ODDS_SOURCES': '2',
    'PUBLISH_TIER_B_MIN_BOOKS': '2',
    'PUBLISH_TIER_B_MIN_CONTEXT_SOURCES': '2',
    'CONTROLLED_FALLBACK_TELEGRAM_ALLOW_TIER_B': 'true',
    'CONTROLLED_FALLBACK_REQUIRE_2_BOOKS_FOR_TELEGRAM': 'true',
    'CONTROLLED_FALLBACK_REQUIRE_2_ODDS_SOURCES_FOR_TELEGRAM': 'true',
    'CONTROLLED_FALLBACK_REQUIRE_2_CONTEXT_SOURCES_FOR_TELEGRAM': 'true',
    'CONTROLLED_FALLBACK_REQUIRE_INDEPENDENT_SOURCES': 'true',
    'CONTROLLED_FALLBACK_TIER_B_REQUIRE_INDEPENDENT_SOURCES': 'true',
    'CONTROLLED_FALLBACK_TIER_A_REQUIRE_2_ODDS_SOURCES': 'true',
    'CONTROLLED_FALLBACK_MIN_ODDS_SOURCES': '2',
    'CONTROLLED_FALLBACK_MIN_CONTEXT_SOURCES': '2',
    'CONTROLLED_FALLBACK_MIN_CONFIRMATION_SOURCES': '2',
    'CONTROLLED_FALLBACK_TIER_B_MIN_BOOKS': '2',
    'CONTROLLED_FALLBACK_TIER_B_MIN_ODDS_SOURCES': '2',
    'CONTROLLED_FALLBACK_TIER_B_MIN_CONTEXT_SOURCES': '2',
    'CONTROLLED_FALLBACK_TIER_B_MIN_CONFIRMATION_SOURCES': '2',
    'CONTROLLED_FALLBACK_USE_INVENTORY_CONTEXT_FALLBACK': 'true',
    'CONTROLLED_FALLBACK_TOTAL_POINT_FROM_BUCKET_OFFERS': 'true',
    'MARKET_DERIVED_MIN_BOOKS': '2',
    'MARKET_DERIVED_MIN_SOURCES': '2',
    'MARKET_DERIVED_CONSENSUS_RELIEF_MIN_BOOKS': '2',
    'MARKET_DERIVED_CONSENSUS_RELIEF_MIN_SOURCES': '2',
    'MARKET_DERIVED_ALLOW_FIRST_SNAPSHOT_CANDIDATES': 'false',
    'FALLBACK_PUBLISH_MODE_ENABLED': 'false',
    'MODEL_RELAXED_FALLBACK_ENABLED': 'false',
    'FORCE_PUBLISH_WHEN_EMPTY_ENABLED': 'false',
    'QUALITY_EMERGENCY_PUBLISH_ENABLED': 'false',
    'QUALITY_LAST_RESORT_PUBLISH_ENABLED': 'false',
    'HISTORICAL_SEGMENT_RELIEF_ENABLED': 'false',
}


def _append_github_env(values: dict[str, str]) -> None:
    for key, value in values.items():
        os.environ[key] = value
    github_env = os.getenv('GITHUB_ENV')
    if github_env:
        with open(github_env, 'a', encoding='utf-8') as fh:
            for key in sorted(values):
                fh.write(f'{key}={values[key]}\n')
    else:
        for key in sorted(values):
            print(f'{key}={values[key]}')


def main() -> int:
    _append_github_env(ENV_UPDATES)
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        'created_at_utc': datetime.now(timezone.utc).isoformat(),
        'policy': 'publication_totals_spreads_only_with_public_total_line_contract',
        'runtime_policy_version': RUNTIME_POLICY_VERSION,
        'allowed_publication_families': ['totals', 'spreads'],
        'blocked_publication_families': ['h2h', 'btts', 'dnb', 'doubleChance', 'teamTotals'],
        'env_updates': ENV_UPDATES,
        'notes': [
            'Only totals and spreads/handicaps may be published.',
            'Public totals must be whole or .5 lines; .25/.75 Asian totals are analysis-only.',
            'Both A-tier and B-tier require 2 independent odds sources, 2 bookmaker prices and 2 context sources before Telegram.',
            'Inventory target overrides are pinned here because the workflow calls this script before run and fallback.',
        ],
    }
    OUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
