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
RUNTIME_POLICY_VERSION = 'harizon-runtime-policy-v7-signals-totals-spreads'

ENV_UPDATES = {
    'HARIZON_RUNTIME_POLICY_VERSION': RUNTIME_POLICY_VERSION,
    'HARIZON_EFFECTIVE_RUNTIME_POLICY_VERSION': RUNTIME_POLICY_VERSION,
    'MARKET_FAMILY_PUBLICATION_GUARD_ENABLED': 'true',
    'PUBLICATION_ALLOWED_MARKET_FAMILIES': ALLOWED,
    'HARIZON_ALLOWED_PUBLICATION_FAMILIES': ALLOWED,
    'MAIN_PUBLISH_ALLOWED_FAMILIES': ALLOWED,
    'TELEGRAM_ALLOWED_MARKET_FAMILIES': ALLOWED,
    'CONTROLLED_FALLBACK_ALLOWED_FAMILIES': ALLOWED,
    'CONTROLLED_FALLBACK_SINGLE_LINE_CONTEXT_MODE_ENABLED': 'true',
    'CORE_LINE_BOOKMAKER_UNIVERSE_ALLOW_SINGLE_SOURCE': 'true',
    'CANDIDATE_FACTORY_ALLOW_SINGLE_LINE_FOR_CONTROLLED_FALLBACK': 'true',
    'CONTROLLED_FALLBACK_SINGLE_LINE_CONTEXT_ALLOWED_FAMILIES': 'totals,spreads',
    'CONTROLLED_FALLBACK_SINGLE_LINE_MIN_ODDS_SOURCES': '1',
    'CONTROLLED_FALLBACK_SINGLE_LINE_MIN_PRICE_CONFIRMATIONS': '2',
    'CONTROLLED_FALLBACK_SINGLE_LINE_MIN_BOOKS': '2',
    'CONTROLLED_FALLBACK_SINGLE_LINE_MIN_CONTEXT_SOURCES': '3',
    'CONTROLLED_FALLBACK_SINGLE_LINE_MIN_EDGE_PP': '4.0',
    'CONTROLLED_FALLBACK_SINGLE_LINE_MIN_EV_PCT': '7.0',
    'CONTROLLED_FALLBACK_SINGLE_LINE_MIN_CONFIDENCE': '76.0',
    'CONTROLLED_FALLBACK_SINGLE_LINE_MIN_QUALITY': '78.0',
    'CONTROLLED_FALLBACK_SINGLE_LINE_REQUIRE_XG_SANITY': 'true',
    'CONTROLLED_FALLBACK_TIER_B_MIN_BOOKS': '2',
    'CONTROLLED_FALLBACK_TIER_B_MIN_EDGE_PP': '4.0',
    'CONTROLLED_FALLBACK_TIER_B_MIN_EV_PCT': '7.0',
    'CONTROLLED_FALLBACK_TIER_B_MIN_CONFIDENCE': '76.0',
    'CONTROLLED_FALLBACK_TIER_B_MIN_QUALITY': '78.0',

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
    # Analysis remains broad so diagnostics can still show why non-public
    # markets looked attractive, but publication remains narrow.
    'ANALYSIS_ALLOWED_MARKET_FAMILIES': 'h2h,totals,spreads,btts,dnb,doubleChance,teamTotals',
    # Runtime signal-stack switches. These are repeated here because older
    # workflow/budget layers call this script but not always the full runtime
    # policy script.
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
        'policy': 'publication_totals_spreads_only_with_signal_runtime_contract',
        'runtime_policy_version': RUNTIME_POLICY_VERSION,
        'allowed_publication_families': ['totals', 'spreads'],
        'blocked_publication_families': ['h2h', 'btts', 'dnb', 'doubleChance', 'teamTotals'],
        'env_updates': ENV_UPDATES,
        'notes': [
            'Only totals and spreads/handicaps may be published.',
            'H2H/P1/P2/X, BTTS, DNB, double chance and team totals are analysis-only.',
            'market_family_publication_guard also blocks Telegram text as last-mile safety.',
            'Runtime signal-stack switches are pinned here because the workflow calls this script before run and fallback.',
            'Bzzoiro is forced through the predictions endpoint and smoke calls are skipped to preserve request budget.',
        ],
    }
    OUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
