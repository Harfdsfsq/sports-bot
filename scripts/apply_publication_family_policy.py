from __future__ import annotations

"""Apply the HARIZON publication-market family contract.

The bot may analyze all supported markets, but public Telegram picks are limited
strictly to totals and spreads/handicaps. This prevents H2H/P1/P2/X, BTTS, DNB,
double chance and team totals from leaking through main publishing or controlled
fallback.
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path('.').resolve()
OUT_PATH = ROOT / '.data' / 'exports' / 'latest-publication-family-policy.json'
ALLOWED = 'totals,spreads'

ENV_UPDATES = {
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
    # Analysis remains broad so diagnostics can still show why non-public
    # markets looked attractive, but publication remains narrow.
    'ANALYSIS_ALLOWED_MARKET_FAMILIES': 'h2h,totals,spreads,btts,dnb,doubleChance,teamTotals',
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
        'policy': 'publication_totals_spreads_only',
        'allowed_publication_families': ['totals', 'spreads'],
        'blocked_publication_families': ['h2h', 'btts', 'dnb', 'doubleChance', 'teamTotals'],
        'env_updates': ENV_UPDATES,
        'notes': [
            'Only totals and spreads/handicaps may be published.',
            'H2H/P1/P2/X, BTTS, DNB, double chance and team totals are analysis-only.',
            'market_family_publication_guard also blocks Telegram text as last-mile safety.',
        ],
    }
    OUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
