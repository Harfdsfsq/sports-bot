from __future__ import annotations

"""Apply HARIZON publication-market family and A-tier-only public policy.

Public Telegram picks are A-tier only. B-tier candidates are not deleted: they
remain available for watchlist diagnostics, lifecycle state, gap reports and
promotion into A-tier after more evidence is collected.
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path('.').resolve()
OUT_PATH = ROOT / '.data' / 'exports' / 'latest-publication-family-policy.json'
PATCH_REPORT_PATH = ROOT / '.data' / 'exports' / 'latest-a-tier-only-publication-patch.json'
ALLOWED = 'totals,spreads'
B_WATCH_ONLY_SENTINEL = '__b_tier_watch_only_no_publication__'
RUNTIME_POLICY_VERSION = 'harizon-runtime-policy-v15-a-tier-only-b-watchlist'

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
    # B-tier is intentionally not an allowed final-publication family. The
    # sentinel keeps B-tier rows visible as rejected/watchlist candidates instead
    # of silently treating them as public-safe.
    'CONTROLLED_FALLBACK_TIER_B_ALLOWED_FAMILIES': B_WATCH_ONLY_SENTINEL,
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
    'CONTROLLED_FALLBACK_REQUIRE_PUBLIC_TOTAL_LINE': 'true',
    'CONTROLLED_FALLBACK_REQUIRE_TOTAL_POINT_FOR_PUBLICATION': 'true',
    'PROMOTE_B_COVER_FILTER_BY_TIME': 'true',
    'ANALYSIS_ALLOWED_MARKET_FAMILIES': 'h2h,totals,spreads,btts,dnb,doubleChance,teamTotals',
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
    'DAY_INVENTORY_TARGET_SIZE': '300',
    'DAY_INVENTORY_MAX_MATCHES': '300',
    'DAY_INVENTORY_ODDS_API_IO_TARGET_MATCHES': '300',
    'DAY_INVENTORY_MULTI_SOURCE_MAX_MATCHES': '300',
    'MAX_MATCHES_FOR_ODDS_FETCH': '300',

    # A-tier-only public publication contract.
    'PUBLISH_ALLOW_B_TIER': 'false',
    'PUBLISH_B_TIER_WATCH_ONLY': 'true',
    'PUBLISH_COVERAGE_TIER_MODE': 'a_only_publish_b_watchlist',
    'MIN_BOOKS_PUBLISH': '2',
    'PUBLISH_MIN_BOOKS': '2',
    'MIN_SOURCES_PUBLISH': '2',
    'PUBLISH_MIN_ODDS_SOURCES': '2',
    'MIN_CONTEXT_SOURCES_PUBLISH': '2',
    'PUBLISH_MIN_CONTEXT_SOURCES': '2',
    'PUBLISH_TIER_A_MIN_ODDS_SOURCES': '2',
    'PUBLISH_TIER_A_MIN_BOOKS': '2',
    'PUBLISH_TIER_A_MIN_CONTEXT_SOURCES': '2',

    # B-tier evidence is retained for watchlist/promotion only.
    'PUBLISH_TIER_B_MIN_ODDS_SOURCES': '1',
    'PUBLISH_TIER_B_MIN_BOOKS': '2',
    'PUBLISH_TIER_B_MIN_CONTEXT_SOURCES': '1',
    'CONTROLLED_FALLBACK_TELEGRAM_ALLOW_TIER_B': 'false',
    'CONTROLLED_FALLBACK_TIER_B_WATCH_ONLY': 'true',
    'CONTROLLED_FALLBACK_TIER_B_PUBLISH_ENABLED': 'false',
    'CONTROLLED_FALLBACK_REQUIRE_2_BOOKS_FOR_TELEGRAM': 'true',
    'CONTROLLED_FALLBACK_TIER_B_REQUIRE_2_BOOKS_FOR_TELEGRAM': 'true',
    'CONTROLLED_FALLBACK_REQUIRE_2_ODDS_SOURCES_FOR_TELEGRAM': 'true',
    'CONTROLLED_FALLBACK_REQUIRE_2_CONTEXT_SOURCES_FOR_TELEGRAM': 'true',
    'CONTROLLED_FALLBACK_REQUIRE_INDEPENDENT_SOURCES': 'true',
    'CONTROLLED_FALLBACK_TIER_B_REQUIRE_INDEPENDENT_SOURCES': 'true',
    'CONTROLLED_FALLBACK_TIER_A_REQUIRE_2_ODDS_SOURCES': 'true',
    'CONTROLLED_FALLBACK_TIER_A_MIN_ODDS_SOURCES': '2',
    'CONTROLLED_FALLBACK_TIER_A_MIN_BOOKS': '2',
    'CONTROLLED_FALLBACK_TIER_A_MIN_CONTEXT_SOURCES': '2',
    'CONTROLLED_FALLBACK_MIN_ODDS_SOURCES': '2',
    'CONTROLLED_FALLBACK_MIN_CONTEXT_SOURCES': '2',
    'CONTROLLED_FALLBACK_MIN_CONFIRMATION_SOURCES': '2',
    'CONTROLLED_FALLBACK_TIER_B_MIN_BOOKS': '2',
    'CONTROLLED_FALLBACK_TIER_B_MIN_BOOKMAKERS': '2',
    'CONTROLLED_FALLBACK_TIER_B_MIN_ODDS_SOURCES': '1',
    'CONTROLLED_FALLBACK_TIER_B_MIN_CONTEXT_SOURCES': '1',
    'CONTROLLED_FALLBACK_TIER_B_MIN_CONFIRMATION_SOURCES': '1',
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
        os.environ[key] = str(value)
    github_env = os.getenv('GITHUB_ENV')
    if github_env:
        with open(github_env, 'a', encoding='utf-8') as fh:
            for key in sorted(values):
                fh.write(f'{key}={values[key]}\n')
    else:
        for key in sorted(values):
            print(f'{key}={values[key]}')


def _patch_controlled_fallback_script() -> dict[str, Any]:
    """Add an idempotent B-tier watch-only guard to the fallback publisher.

    This avoids replacing the large publisher file in the archive. The workflow
    already calls this policy script before fallback, so the guard is applied on
    every run even if older env layers try to re-enable B-tier.
    """
    path = ROOT / 'scripts' / 'publish_controlled_fallback.py'
    report: dict[str, Any] = {
        'enabled': True,
        'path': str(path),
        'changed': False,
        'status': 'ok',
        'reason': 'tier_b_watch_only_final_guard',
    }
    try:
        text = path.read_text(encoding='utf-8')
    except Exception as exc:
        report.update({'status': 'read_error', 'error': f'{type(exc).__name__}: {exc}'})
        return report

    guard = (
        '    if tier_name == "B" and env_bool("CONTROLLED_FALLBACK_TIER_B_WATCH_ONLY", True):\n'
        '        reasons.append("tier_b_watch_only")\n\n'
    )
    if 'tier_b_watch_only' in text:
        report['status'] = 'already_applied'
        return report
    marker = '    tier_name = tier.replace("уровень ", "").strip().upper()\n\n'
    if marker not in text:
        report.update({'status': 'marker_missing', 'marker': marker})
        return report
    text = text.replace(marker, marker + guard, 1)
    path.write_text(text, encoding='utf-8')
    report['changed'] = True
    return report


def main() -> int:
    _append_github_env(ENV_UPDATES)
    patch_report = _patch_controlled_fallback_script()
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    PATCH_REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    PATCH_REPORT_PATH.write_text(json.dumps(patch_report, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    payload = {
        'created_at_utc': datetime.now(timezone.utc).isoformat(),
        'policy': 'publication_totals_spreads_only_a_tier_public_b_tier_watchlist',
        'runtime_policy_version': RUNTIME_POLICY_VERSION,
        'allowed_publication_families': ['totals', 'spreads'],
        'blocked_publication_families': ['h2h', 'btts', 'dnb', 'doubleChance', 'teamTotals'],
        'public_publication_tier': 'A-only',
        'b_tier_mode': 'watchlist_only',
        'b_tier_publication_block': {
            'sentinel': B_WATCH_ONLY_SENTINEL,
            'final_guard_reason': 'tier_b_watch_only',
        },
        'env_updates': ENV_UPDATES,
        'fallback_script_patch': patch_report,
        'notes': [
            'Only totals and spreads/handicaps may be published.',
            'A-tier publication requires 2 odds/line sources, 2 bookmaker prices and 2 context sources.',
            'B-tier is retained only for watchlist, diagnostics, lifecycle accumulation and promotion into A-tier.',
            'B-tier candidate generation and gap reports stay enabled; Telegram publication is blocked.',
        ],
    }
    OUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
