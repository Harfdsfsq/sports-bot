from __future__ import annotations

"""Quality-first Telegram publication policy.

The historical ledger shows that the bot's weak spots are high odds, non-core
leagues, single-book signals and non-totals markets. This runtime layer keeps the
full data/candidate pipeline available for diagnostics and learning, but limits
Telegram publication to the currently best-performing family: match totals.

It is intentionally applied after volume/no-cap policy and before the bot run.
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path('.').resolve()
UTC = timezone.utc
GITHUB_ENV = os.getenv('GITHUB_ENV')
OUT = ROOT / '.data' / 'exports' / 'latest-quality-first-runtime-policy.json'
POLICY_VERSION = 'v2-quality-first-totals-b-tier-xg-10p5'


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')


def append_env(env: dict[str, str]) -> None:
    if GITHUB_ENV:
        with open(GITHUB_ENV, 'a', encoding='utf-8') as fh:
            for key in sorted(env):
                fh.write(f'{key}={env[key]}\n')
    else:
        for key in sorted(env):
            print(f'{key}={env[key]}')


def main() -> int:
    env = {
        'QUALITY_FIRST_RUNTIME_POLICY_ACTIVE': 'true',
        'QUALITY_FIRST_RUNTIME_POLICY_VERSION': POLICY_VERSION,

        # Keep full candidate generation/diagnostics, but publish only totals to Telegram.
        'TELEGRAM_MARKETS': 'totals',
        'CONTROLLED_FALLBACK_ALLOWED_FAMILIES': 'totals',
        'CONTROLLED_FALLBACK_TIER_A_ALLOWED_FAMILIES': 'totals',
        'CONTROLLED_FALLBACK_TIER_B_ALLOWED_FAMILIES': 'totals',
        'CONTROLLED_FALLBACK_TIER_C_ALLOWED_FAMILIES': '',
        'CONTROLLED_FALLBACK_TIER_C_PUBLISH_ENABLED': 'false',
        'DAILY_BEST5_ALLOWED_FAMILIES': 'totals',
        'DAILY_BEST5_TIER_A_ALLOWED_FAMILIES': 'totals',
        'DAILY_BEST5_TIER_B_ALLOWED_FAMILIES': 'totals',

        # Avoid historical failure modes: high odds and single-book/proxy signals.
        'ODDS_MIN': '1.45',
        'ODDS_MAX': '2.35',
        'TARGET_ODDS_HARD_MIN': '1.45',
        'TARGET_ODDS_HARD_MAX': '2.35',
        'CONTROLLED_FALLBACK_GLOBAL_MIN_ODDS': '1.45',
        'CONTROLLED_FALLBACK_GLOBAL_MAX_ODDS': '2.35',
        'CONTROLLED_FALLBACK_REQUIRE_2_BOOKS_FOR_TELEGRAM': 'true',
        'CONTROLLED_FALLBACK_REJECT_PROXY_SINGLE_BOOK': 'true',
        'CONTROLLED_FALLBACK_REQUIRE_MARKET_CONFIRMATION_FOR_PROXY': 'true',
        'CONTROLLED_FALLBACK_PROXY_SINGLE_SOURCE_STRICT': 'true',

        # Tier A: strong totals only.
        'CONTROLLED_FALLBACK_TIER_A_MIN_BOOKS': '2',
        'CONTROLLED_FALLBACK_TIER_A_MIN_CONFIDENCE': '66.0',
        'CONTROLLED_FALLBACK_TIER_A_MIN_QUALITY': '66.0',
        'CONTROLLED_FALLBACK_TIER_A_MIN_EDGE_PP': '3.8',
        'CONTROLLED_FALLBACK_TIER_A_MIN_EV_PCT': '7.5',
        'CONTROLLED_FALLBACK_TIER_A_MIN_PUBLICATION_SCORE': '18',
        'CONTROLLED_FALLBACK_TIER_A_MAX_ODDS': '2.25',

        # Tier B: target-5 practical gate. Still requires 2 books, positive value and xG sanity.
        'CONTROLLED_FALLBACK_TIER_B_MIN_BOOKS': '2',
        'CONTROLLED_FALLBACK_TIER_B_MIN_CONFIDENCE': '64.0',
        'CONTROLLED_FALLBACK_TIER_B_MIN_QUALITY': '62.0',
        'CONTROLLED_FALLBACK_TIER_B_MIN_EDGE_PP': '3.5',
        'CONTROLLED_FALLBACK_TIER_B_MIN_EV_PCT': '7.0',
        'CONTROLLED_FALLBACK_TIER_B_MIN_PUBLICATION_SCORE': '14',
        'CONTROLLED_FALLBACK_TIER_B_MAX_ODDS': '2.35',

        # Final value gates.
        'CONTROLLED_FALLBACK_FINAL_MIN_EDGE_PP': '3.2',
        'CONTROLLED_FALLBACK_FINAL_MIN_EV_PCT': '6.8',
        'CONTROLLED_FALLBACK_EXTRA_PICK_MIN_EDGE_PP': '3.5',
        'CONTROLLED_FALLBACK_EXTRA_PICK_MIN_EV_PCT': '7.0',
        'CONTROLLED_FALLBACK_EXTRA_PICK_MIN_CONFIDENCE': '64.0',
        'CONTROLLED_FALLBACK_PROXY_SINGLE_SOURCE_MIN_CONFIDENCE': '64.0',
        'CONTROLLED_FALLBACK_PROXY_SINGLE_SOURCE_MIN_EDGE_PP': '3.5',
        'CONTROLLED_FALLBACK_PROXY_SINGLE_SOURCE_MIN_EV_PCT': '7.0',

        # Totals sanity remains mandatory. Tier B is allowed a small extra proxy-xG gap,
        # but the hard reject still blocks obvious model-vs-xG disagreements.
        'CONTROLLED_FALLBACK_XG_SANITY_ENABLED': 'true',
        'CONTROLLED_FALLBACK_REQUIRE_TOTALS_SANITY_FOR_TELEGRAM': 'true',
        'CONTROLLED_FALLBACK_XG_DIRECTION_MARGIN': '0.18',
        'CONTROLLED_FALLBACK_XG_HARD_REJECT_GAP_PP': '12.0',
        'CONTROLLED_FALLBACK_TIER_A_REQUIRE_XG_SANITY': 'true',
        'CONTROLLED_FALLBACK_TIER_A_MAX_XG_GAP_PP': '6.0',
        'CONTROLLED_FALLBACK_TIER_B_MAX_XG_GAP_PP': '10.5',

        # Keep target-5 cadence, but do not force volume over quality.
        'DAILY_TOP5_TARGET_PICKS': os.getenv('DAILY_TOP5_TARGET_PICKS', '5'),
        'DAILY_BEST5_TARGET_PICKS': os.getenv('DAILY_BEST5_TARGET_PICKS', '5'),
        'DAILY_TOP5_NO_HARD_CAP': 'true',
        'VOLUME_NO_DAILY_HARD_CAP': 'true',
        'CONTROLLED_FALLBACK_MAX_PICKS_PER_RUN': os.getenv('CONTROLLED_FALLBACK_MAX_PICKS_PER_RUN', '2'),
        'CONTROLLED_FALLBACK_ABSOLUTE_MAX_PICKS_PER_RUN': os.getenv('CONTROLLED_FALLBACK_ABSOLUTE_MAX_PICKS_PER_RUN', '2'),
        'MAX_PICKS_PER_RUN': os.getenv('MAX_PICKS_PER_RUN', '2'),

        # Bankroll: lower flat exposure while collecting a clean post-policy sample.
        'BANKROLL_KELLY_ENABLED': 'false',
        'BANKROLL_FLAT_STAKE_PCT': '0.50',
        'BANKROLL_MAX_STAKE_PCT': '0.75',
        'BANKROLL_MAX_OPEN_EXPOSURE_PCT': '10',
        'CONTROLLED_FALLBACK_TOTAL_STAKE_CAP_PCT': '2.50',
    }
    append_env(env)

    report = {
        'status': 'ok',
        'version': POLICY_VERSION,
        'updated_at_utc': datetime.now(UTC).isoformat(),
        'applied_env': env,
        'summary': {
            'telegram_publication': 'totals_only',
            'diagnostics': 'all generated candidates can still be tracked in reports/shadow, but weak families are blocked from Telegram',
            'historical_reason': 'totals is the only historically positive family; high_odds, non_core_league and single_book are top loss tags',
            'target': 'about 5 best picks/day when value exists, no forced volume',
            'xg_tuning': 'Tier B totals xG gap raised from 9.0 to 10.5 while hard reject remains 12.0',
        },
    }
    write_json(OUT, report)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
