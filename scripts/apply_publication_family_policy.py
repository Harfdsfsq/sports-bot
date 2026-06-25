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
PATCH_REPORT_PATH = ROOT / '.data' / 'exports' / 'latest-controlled-fallback-b-tier-evidence-patch.json'
ALLOWED = 'totals,spreads'
RUNTIME_POLICY_VERSION = 'harizon-runtime-policy-v14-fallback-b-tier-evidence-normalized'

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
    # Publication contract from project rules: A is strict, B is lighter but still
    # protected by price integrity, value, quality, xG and line movement.
    'PUBLISH_ALLOW_B_TIER': 'true',
    'PUBLISH_COVERAGE_TIER_MODE': 'hybrid',
    'MIN_BOOKS_PUBLISH': '1',
    'PUBLISH_MIN_BOOKS': '1',
    'MIN_SOURCES_PUBLISH': '1',
    'PUBLISH_MIN_ODDS_SOURCES': '1',
    'MIN_CONTEXT_SOURCES_PUBLISH': '1',
    'PUBLISH_MIN_CONTEXT_SOURCES': '1',
    'PUBLISH_TIER_A_MIN_ODDS_SOURCES': '2',
    'PUBLISH_TIER_A_MIN_BOOKS': '2',
    'PUBLISH_TIER_A_MIN_CONTEXT_SOURCES': '2',
    'PUBLISH_TIER_B_MIN_ODDS_SOURCES': '1',
    'PUBLISH_TIER_B_MIN_BOOKS': '1',
    'PUBLISH_TIER_B_MIN_CONTEXT_SOURCES': '1',
    'CONTROLLED_FALLBACK_TELEGRAM_ALLOW_TIER_B': 'true',
    'CONTROLLED_FALLBACK_REQUIRE_2_BOOKS_FOR_TELEGRAM': 'false',
    'CONTROLLED_FALLBACK_TIER_B_REQUIRE_2_BOOKS_FOR_TELEGRAM': 'false',
    'CONTROLLED_FALLBACK_REQUIRE_2_ODDS_SOURCES_FOR_TELEGRAM': 'false',
    'CONTROLLED_FALLBACK_REQUIRE_2_CONTEXT_SOURCES_FOR_TELEGRAM': 'false',
    'CONTROLLED_FALLBACK_REQUIRE_INDEPENDENT_SOURCES': 'false',
    'CONTROLLED_FALLBACK_TIER_B_REQUIRE_INDEPENDENT_SOURCES': 'false',
    'CONTROLLED_FALLBACK_TIER_A_REQUIRE_2_ODDS_SOURCES': 'true',
    'CONTROLLED_FALLBACK_MIN_ODDS_SOURCES': '1',
    'CONTROLLED_FALLBACK_MIN_CONTEXT_SOURCES': '1',
    'CONTROLLED_FALLBACK_MIN_CONFIRMATION_SOURCES': '1',
    'CONTROLLED_FALLBACK_TIER_B_MIN_BOOKS': '1',
    'CONTROLLED_FALLBACK_TIER_B_MIN_BOOKMAKERS': '1',
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
        os.environ[key] = value
    github_env = os.getenv('GITHUB_ENV')
    if github_env:
        with open(github_env, 'a', encoding='utf-8') as fh:
            for key in sorted(values):
                fh.write(f'{key}={values[key]}\n')
    else:
        for key in sorted(values):
            print(f'{key}={values[key]}')


def _patch_controlled_fallback_script() -> dict[str, object]:
    """Keep the standalone fallback script aligned with the A/B contract.

    `publish_controlled_fallback.py` is executed as a standalone script, so normal
    runtime monkey-patches do not reach it.  This small, idempotent source patch is
    applied before the workflow calls fallback.  It fixes two stale assumptions:
    B-tier hard-coded 2-book minimums and missing confirmation aliases for context
    sources already present in rescue rows.
    """
    path = ROOT / 'scripts' / 'publish_controlled_fallback.py'
    report: dict[str, object] = {
        'enabled': True,
        'path': str(path),
        'changed': False,
        'replacements': [],
        'status': 'ok',
    }
    try:
        text = path.read_text(encoding='utf-8')
    except Exception as exc:
        report.update({'status': 'read_error', 'error': f'{type(exc).__name__}: {exc}'})
        return report

    replacements = [
        (
            '        "highlightly": "highlightly",\n',
            '        "highlightly": "highlightly",\n'
            '        "openligadb": "openligadb",\n'
            '        "open_liga_db": "openligadb",\n'
            '        "inventory_context": "inventory_context",\n'
            '        "runtime_context": "runtime_context",\n',
            'confirmation_aliases',
        ),
        (
            '        for field in ("context_sources", "providers", "confirmation_sources"):\n'
            '            for item in _source_values(source_summary.get(field)):\n'
            '                src = normalize_confirmation_source(item)\n'
            '                if src:\n'
            '                    sources.add(src)\n\n'
            '    sources.discard("odds_api_io")\n',
            '        for field in ("context_sources", "providers", "confirmation_sources"):\n'
            '            for item in _source_values(source_summary.get(field)):\n'
            '                src = normalize_confirmation_source(item)\n'
            '                if src:\n'
            '                    sources.add(src)\n'
            '        for nested_key in ("publish_coverage_contract", "publication_tier_contract"):\n'
            '            nested = source_summary.get(nested_key)\n'
            '            if isinstance(nested, dict):\n'
            '                for field in ("context_sources", "confirmation_sources"):\n'
            '                    for item in _source_values(nested.get(field)):\n'
            '                        src = normalize_confirmation_source(item)\n'
            '                        if src:\n'
            '                            sources.add(src)\n'
            '    diagnostics = candidate.get("diagnostics") if isinstance(candidate.get("diagnostics"), dict) else {}\n'
            '    for nested_key in ("publish_coverage_contract", "publication_tier_contract"):\n'
            '        nested = diagnostics.get(nested_key)\n'
            '        if isinstance(nested, dict):\n'
            '            for field in ("context_sources", "confirmation_sources"):\n'
            '                for item in _source_values(nested.get(field)):\n'
            '                    src = normalize_confirmation_source(item)\n'
            '                    if src:\n'
            '                        sources.add(src)\n\n'
            '    sources.discard("odds_api_io")\n',
            'nested_publish_coverage_context_sources',
        ),
        (
            '    min_books = max(2, env_int("CONTROLLED_FALLBACK_TIER_B_MIN_BOOKS", 2))\n',
            '    min_books = max(1, env_int("CONTROLLED_FALLBACK_TIER_B_MIN_BOOKS", 1))\n',
            'tier_b_quorum_min_books',
        ),
        (
            '    # HARIZON rules: A-tier is strict 2/2/2; B-tier is 1 odds/line source,\n'
            '    # 2 bookmakers/price confirmations, and 1 context/confirmation.\n',
            '    # HARIZON rules: A-tier is strict 2/2/2; B-tier is 1 odds/line source,\n'
            '    # 1 bookmaker/price confirmation, and 1 context/confirmation.\n',
            'tier_b_comment',
        ),
        (
            '        min_books = max(2, env_int("CONTROLLED_FALLBACK_TIER_B_MIN_BOOKS", env_int("CONTROLLED_FALLBACK_TIER_B_MIN_BOOKMAKERS", 2)))\n',
            '        min_books = max(1, env_int("CONTROLLED_FALLBACK_TIER_B_MIN_BOOKS", env_int("CONTROLLED_FALLBACK_TIER_B_MIN_BOOKMAKERS", 1)))\n',
            'tier_b_min_books',
        ),
    ]

    for old, new, label in replacements:
        if old in text:
            text = text.replace(old, new, 1)
            report['changed'] = True
            report['replacements'].append(label)  # type: ignore[index]
        elif new in text:
            report['replacements'].append(f'{label}:already_applied')  # type: ignore[index]
        else:
            report['replacements'].append(f'{label}:pattern_missing')  # type: ignore[index]

    if report['changed']:
        path.write_text(text, encoding='utf-8')
    return report


def main() -> int:
    _append_github_env(ENV_UPDATES)
    fallback_patch = _patch_controlled_fallback_script()
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    PATCH_REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    PATCH_REPORT_PATH.write_text(json.dumps(fallback_patch, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    payload = {
        'created_at_utc': datetime.now(timezone.utc).isoformat(),
        'policy': 'publication_totals_spreads_only_with_public_total_line_contract',
        'runtime_policy_version': RUNTIME_POLICY_VERSION,
        'allowed_publication_families': ['totals', 'spreads'],
        'blocked_publication_families': ['h2h', 'btts', 'dnb', 'doubleChance', 'teamTotals'],
        'env_updates': ENV_UPDATES,
        'fallback_script_patch': fallback_patch,
        'notes': [
            'Only totals and spreads/handicaps may be published.',
            'Public totals must be whole or .5 lines; .25/.75 Asian totals are analysis-only.',
            'A-tier requires 2 odds/line sources, 2 bookmaker prices and 2 context sources; B-tier requires 1 odds/line source, 1 bookmaker price and 1 context source.',
            'Inventory target overrides are pinned here because the workflow calls this script before run and fallback.',
        ],
    }
    OUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
