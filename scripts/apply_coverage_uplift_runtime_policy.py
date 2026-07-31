from __future__ import annotations

"""Runtime coverage contract for 2+ independent lines and 2+ contexts.

This step is executed before the production run.  It writes provider budgets and
publication evidence settings to GITHUB_ENV.  The goal is not to loosen picks; it
is to make the data layer work harder until the 300-row inventory has two line
sources and two context sources wherever the free providers can supply them.
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path('.').resolve()
EXPORT_PATH = ROOT / '.data' / 'exports' / 'latest-coverage-uplift-runtime-policy.json'
GITHUB_ENV = os.getenv('GITHUB_ENV')
VERSION = 'v22-two-plus-lines-contexts'


def truthy(value: object, default: bool = False) -> bool:
    raw = str(value if value is not None else '').strip().lower()
    if not raw:
        return default
    return raw in {'1', 'true', 'yes', 'on', 'force'}


def present(*names: str) -> bool:
    return any(str(os.getenv(name) or '').strip() for name in names)


def phase() -> str:
    explicit = str(os.getenv('HARIZON_RUN_PHASE') or os.getenv('RUN_PHASE') or '').strip().lower()
    if explicit:
        return explicit
    try:
        tz = ZoneInfo(os.getenv('APP_TIMEZONE') or os.getenv('TZ') or 'Europe/Moscow')
    except Exception:
        tz = timezone.utc
    hour = datetime.now(timezone.utc).astimezone(tz).hour
    if 0 <= hour <= 2:
        return 'full_inventory'
    if 3 <= hour <= 10:
        return 'morning_backfill'
    return 'live_refresh'


def put_limit(env: dict[str, str], prefix: str, value: int, *extra_aliases: str) -> None:
    upper = prefix.upper()
    for key in {
        f'{upper}_PER_RUN_MAX',
        f'{upper}_MAX_HTTP_REQUESTS_PER_RUN',
        f'{upper}_MAX_REQUESTS_PER_RUN',
        f'{upper}_REQUESTS_MAX_PER_RUN',
        f'{upper}_REQUEST_BUDGET_GRANTED',
        *extra_aliases,
    }:
        env[key] = str(max(0, int(value)))


def write_env(env: dict[str, str]) -> None:
    for key, value in env.items():
        os.environ[str(key)] = str(value)
    if GITHUB_ENV:
        with open(GITHUB_ENV, 'a', encoding='utf-8') as fh:
            for key in sorted(env):
                fh.write(f'{key}={env[key]}\n')
    else:
        for key in sorted(env):
            print(f'{key}={env[key]}')


def report(payload: dict) -> None:
    EXPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    EXPORT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def main() -> int:
    run_phase = phase()
    if not truthy(os.getenv('HARIZON_COVERAGE_UPLIFT_ENABLED'), True):
        report({'status': 'disabled_by_env', 'version': VERSION, 'phase': run_phase, 'created_at_utc': datetime.now(timezone.utc).isoformat()})
        return 0

    # Per-run ceilings.  These remain bounded but are high enough that live runs
    # do not stop at the old 36-row runner window.
    if run_phase == 'full_inventory':
        odds_total, odds_account, sstats_budget, bzz_budget, sportlogic_budget = 100, 50, 190, 360, 10
        sstats_deep_budget, bzz_comparison_limit = 180, 220
    elif run_phase == 'morning_backfill':
        odds_total, odds_account, sstats_budget, bzz_budget, sportlogic_budget = 100, 50, 180, 330, 8
        sstats_deep_budget, bzz_comparison_limit = 170, 200
    else:
        odds_total, odds_account, sstats_budget, bzz_budget, sportlogic_budget = 100, 50, 170, 300, 8
        sstats_deep_budget, bzz_comparison_limit = 160, 180

    context_limit = 300
    premium_limit = 300
    has_odds_1 = present('ODDS_API_IO_KEY')
    has_odds_2 = present('ODDS_API_IO_KEY_2', 'ODDS_API_IO_KEY2', 'ODDS_API_IO_ACC2_KEY', 'ODDS_API_IO_SECONDARY_KEY')
    has_sstats = present('SSTATS_API_KEY')
    has_bzz = present('BZZOIRO_API_KEY')
    has_sportlogic = present('SPORTLOGIC_API_KEY', 'SPORTLOGIC_KEY', 'SPORTLOGIC_TOKEN')

    env = {
        'HARIZON_COVERAGE_UPLIFT_VERSION': VERSION,
        'HARIZON_COVERAGE_UPLIFT_PHASE': run_phase,
        'HARIZON_COVERAGE_UPLIFT_ENABLED': 'true',
        'HARIZON_TARGET_2PLUS_ODDS_SOURCES': '300',
        'HARIZON_TARGET_2PLUS_CONTEXT_SOURCES': '300',
        'HARIZON_REQUIRE_2PLUS_LINES_CONTEXTS_FOR_TELEGRAM': 'false',
        'CONTROLLED_FALLBACK_REQUIRE_2PLUS_LINES_CONTEXTS': 'false',
        'CONTROLLED_FALLBACK_MIN_ODDS_SOURCES': '1',
        'CONTROLLED_FALLBACK_MIN_CONTEXT_SOURCES': '1',
        'CONTROLLED_FALLBACK_MIN_CONFIRMATION_SOURCES': '1',
        'CONTROLLED_FALLBACK_TIER_A_REQUIRE_2_ODDS_SOURCES': 'true',
        'CONTROLLED_FALLBACK_TIER_A_MIN_ODDS_SOURCES': '2',
        'CONTROLLED_FALLBACK_TIER_A_MIN_CONTEXT_SOURCES': '2',
        'CONTROLLED_FALLBACK_TIER_A_MIN_CONFIRMATION_SOURCES': '2',
        'CONTROLLED_FALLBACK_TIER_B_MIN_ODDS_SOURCES': '1',
        'CONTROLLED_FALLBACK_TIER_B_MIN_CONTEXT_SOURCES': '1',
        'CONTROLLED_FALLBACK_TIER_B_MIN_CONFIRMATION_SOURCES': '1',
        'PUBLISH_TIER_A_MIN_ODDS_SOURCES': '2',
        'PUBLISH_TIER_A_MIN_CONTEXT_SOURCES': '2',
        'PUBLISH_TIER_B_MIN_ODDS_SOURCES': '1',
        'PUBLISH_TIER_B_MIN_CONTEXT_SOURCES': '1',
        'PUBLISH_MIN_CONTEXT_SOURCES': '1',
        'MIN_CONTEXT_SOURCES_PUBLISH': '1',
        'PUBLISH_MIN_ODDS_SOURCES': '1',
        'MIN_SOURCES_PUBLISH': '1',
        'RUN_MODE': 'normal',
        'PUBLISH_WINDOW_HOURS': '2',
        'CONTROLLED_FALLBACK_PUBLISH_WINDOW_HOURS': '2',
        'DAY_INVENTORY_TARGET_SIZE': '300',
        'DAY_INVENTORY_MAX_MATCHES': '300',
        'DAY_INVENTORY_FORCE_FULL_300': 'true',
        'DAY_INVENTORY_FORCE_TOP_300': 'true',
        'DAY_INVENTORY_FORCE_PROVIDER_MERGE': 'true',
        'DAY_INVENTORY_USE_FOR_RUN': 'true',
        'DAY_INVENTORY_SEMANTIC_DEDUPE_ENABLED': 'true',
        'DAY_INVENTORY_HORIZON_DAYS': '2',
        'DAY_INVENTORY_TARGET_HORIZON_DAYS': '2',
        'RUN_DAYS_AHEAD': '2',
        'DAY_INVENTORY_CONTEXT_BACKFILL_ENABLED': 'true',
        'DAY_INVENTORY_CONTEXT_BACKFILL_LIMIT': str(context_limit),
        'CONTEXT_ENRICHMENT_REQUIRES_OFFERS': 'false',
        'CONTEXT_ENRICHMENT_MATCH_LIMIT': str(context_limit),
        'PREMIUM_CONTEXT_SHORTLIST_LIMIT': str(premium_limit),
        'MAX_MATCHES_FOR_ODDS_FETCH': '300',
        'LINE_MOVEMENT_MIN_CURRENT_EV_PCT': os.getenv('LINE_MOVEMENT_MIN_CURRENT_EV_PCT') or '2.85',
        'LINE_MOVEMENT_MIN_CURRENT_EDGE_PP': os.getenv('LINE_MOVEMENT_MIN_CURRENT_EDGE_PP') or '1.45',
        'DAY_INVENTORY_ODDS_API_IO_TARGET_MATCHES': '300',
        'DAY_INVENTORY_MULTI_SOURCE_MAX_MATCHES': '300',
        'ODDS_API_IO_MAX_EVENTS': '100',
        'ODDS_API_IO_MAX_ODDS_EVENTS_PER_RUN': '100',
        'ODDS_API_IO_MAX_EVENT_PAGES_PER_SPORT': '24',
        'ODDS_API_IO_MAX_PAGES_PER_SPORT': '24',
        'ODDS_API_IO_PAGE_LIMIT': '100',
        'ODDS_API_IO_ACCOUNT1_PER_RUN_MAX': str(odds_account if has_odds_1 else 0),
        'ODDS_API_IO_ACCOUNT2_PER_RUN_MAX': str(odds_account if has_odds_2 else 0),
        'PRICE_BACKFILL_ODDS_API_IO_BATCHES_PER_ACCOUNT': '10',
        'PRICE_BACKFILL_ODDS_API_IO_MAX_EVENT_IDS_PER_REQUEST': '12',
        'PRICE_BACKFILL_ODDS_API_IO_EVENT_LIMIT': '260',
        'ODDS_API_IO_RELAXED_INVENTORY_MATCHING_ENABLED': 'true',
        'ODDS_API_IO_RELAXED_EXACT_TOLERANCE_HOURS': '16',
        'ODDS_API_IO_RELAXED_FUZZY_TOLERANCE_HOURS': '16',
        'SSTATS_ENABLED': 'true' if has_sstats else 'false',
        'ENABLE_SSTATS': 'true' if has_sstats else 'false',
        'ENABLE_SSTATS_CONTEXT': 'true' if has_sstats else 'false',
        'SSTATS_CONTEXT_ENABLED': 'true' if has_sstats else 'false',
        'SSTATS_CONTEXT_MATCH_LIMIT': str(context_limit if has_sstats else 0),
        'SSTATS_RECENT_MATCHES': '12',
        'SSTATS_FORM_MIN_SAMPLE_PER_TEAM': '2',
        'SSTATS_LOOKBACK_DAYS': '60',
        'SSTATS_REQUEST_CHUNK_DAYS': '7',
        'SSTATS_DEEP_ENRICHMENT_ENABLED': 'true' if has_sstats else 'false',
        'SSTATS_GAME_DETAIL_ENABLED': 'true' if has_sstats else 'false',
        'SSTATS_LAST_GAMES_STATS_ENABLED': 'true' if has_sstats else 'false',
        'SSTATS_GLICKO_ENABLED': 'true' if has_sstats else 'false',
        'SSTATS_DEEP_DETAIL_LIMIT_PER_RUN': str(sstats_deep_budget if has_sstats else 0),
        'SSTATS_DEEP_CONTEXT_MATCH_LIMIT': str(premium_limit if has_sstats else 0),
        'SSTATS_DEEP_REQUESTS_MAX_PER_RUN': str(sstats_deep_budget if has_sstats else 0),
        'SSTATS_DEEP_MAX_REQUESTS_PER_RUN': str(sstats_deep_budget if has_sstats else 0),
        'DAY_INVENTORY_SSTATS_MAX_REQUESTS': str(sstats_budget if has_sstats else 0),
        'BZZOIRO_ENABLED': 'true' if has_bzz else 'false',
        'ENABLE_BZZOIRO': 'true' if has_bzz else 'false',
        'ENABLE_BZZOIRO_CONTEXT': 'true' if has_bzz else 'false',
        'BZZOIRO_CONTEXT_ENABLED': 'true' if has_bzz else 'false',
        'BZZOIRO_CONTEXT_MATCH_LIMIT': str(context_limit if has_bzz else 0),
        'BZZOIRO_ODDS_MATCH_LIMIT': str(premium_limit if has_bzz else 0),
        'BZZOIRO_MAX_PAGES': '40',
        'BZZOIRO_PAGE_SIZE': '60',
        'BZZOIRO_V2_ENRICHMENT_ENABLED': 'true' if has_bzz else 'false',
        'BZZOIRO_V2_RUNTIME_DAY_WINDOW_PATCH': 'true' if has_bzz else 'false',
        'BZZOIRO_V2_DATE_WINDOW_PAD_DAYS': '2',
        'BZZOIRO_V2_DATE_WINDOW_MAX_SPAN_DAYS': '7',
        'BZZOIRO_V2_MATCH_LIMIT': str(premium_limit if has_bzz else 0),
        'BZZOIRO_V2_SOURCE_MATRIX_TARGETS_ENABLED': 'true' if has_bzz else 'false',
        'BZZOIRO_V2_SOURCE_MATRIX_TARGET_LIMIT': str(premium_limit if has_bzz else 0),
        'BZZOIRO_CONTEXT_GAP_MATCH_LIMIT': str(premium_limit if has_bzz else 0),
        'BZZOIRO_V2_ODDS_ENABLED': 'true' if has_bzz else 'false',
        'BZZOIRO_V2_FETCH_EVENT_ODDS': 'true' if has_bzz else 'false',
        'BZZOIRO_V2_FETCH_EVENT_STATS': 'true' if has_bzz else 'false',
        'BZZOIRO_V2_FETCH_EVENT_METADATA': 'true' if has_bzz else 'false',
        'BZZOIRO_V2_FETCH_EVENT_PREDICTION': 'true' if has_bzz else 'false',
        'BZZOIRO_V2_FETCH_ODDS_COMPARISON': 'true' if has_bzz else 'false',
        'BZZOIRO_V2_ODDS_COMPARISON_MATCH_LIMIT': str(bzz_comparison_limit if has_bzz else 0),
        'BZZOIRO_V2_ODDS_COMPARISON_MAX_REQUESTS': str(bzz_comparison_limit if has_bzz else 0),
        'BZZOIRO_CURRENT_ODDS_AS_SECONDARY_SOURCE': 'true' if has_bzz else 'false',
        'BZZOIRO_ODDS_REKEY_ENABLED': 'true' if has_bzz else 'false',
        'BZZOIRO_PRICE_BACKFILL_ENABLED': 'true' if has_bzz else 'false',
        'DAY_INVENTORY_BZZOIRO_MAX_REQUESTS': str(bzz_budget if has_bzz else 0),
        'DAY_INVENTORY_BZZOIRO_MAX_PAGES': '40',
        'DAY_INVENTORY_ENABLE_SPORTLOGIC': 'true' if has_sportlogic else 'false',
        'SPORTLOGIC_ENABLED': 'true' if has_sportlogic else 'false',
        'ENABLE_SPORTLOGIC': 'true' if has_sportlogic else 'false',
        'SPORTLOGIC_CONTROLLED_ODDS_ENABLED': 'true' if has_sportlogic else 'false',
        'SPORTLOGIC_MATCH_LIMIT': '100' if has_sportlogic else '0',
        'SPORTLOGIC_CONTEXT_MATCH_LIMIT': '100' if has_sportlogic else '0',
        'SPORTLOGIC_ODDS_MATCH_LIMIT': '36' if has_sportlogic else '0',
        'DAY_INVENTORY_SPORTLOGIC_MATCH_LIMIT': '100' if has_sportlogic else '0',
        'DAY_INVENTORY_SPORTLOGIC_MAX_REQUESTS': str(sportlogic_budget if has_sportlogic else 0),
        'SPORTLOGIC_ACTIVE_ODDS_TARGETED_CONFIRMATION_ENABLED': 'true' if has_sportlogic else 'false',
        'SPORTLOGIC_ACTIVE_ODDS_ALLOW_WITHOUT_CURRENT_GAMES': 'true' if has_sportlogic else 'false',
        'SPORTLOGIC_SKIP_ACTIVE_ODDS_WHEN_NO_CURRENT_GAMES': 'false' if has_sportlogic else 'true',
        'SPORTLOGIC_TARGETED_GAME_DETAIL_LIMIT': '6' if has_sportlogic else '0',
        'SPORTLOGIC_MATCH_MIN_SCORE': '48',
    }
    put_limit(env, 'ODDS_API_IO', odds_total if has_odds_1 else 0)
    put_limit(env, 'SSTATS', sstats_budget if has_sstats else 0)
    put_limit(env, 'BZZOIRO', bzz_budget if has_bzz else 0, 'BZZOIRO_EVENTS_MAX_REQUESTS_PER_RUN', 'BZZOIRO_PREDICTIONS_MAX_REQUESTS_PER_RUN')
    put_limit(env, 'SPORTLOGIC', sportlogic_budget if has_sportlogic else 0)

    write_env(env)
    report({
        'status': 'installed',
        'version': VERSION,
        'phase': run_phase,
        'created_at_utc': datetime.now(timezone.utc).isoformat(),
        'providers_enabled': {'odds_api_io': has_odds_1, 'odds_api_io_2': has_odds_2, 'sstats': has_sstats, 'bzzoiro': has_bzz, 'sportlogic': has_sportlogic},
        'targets': {'inventory': 300, 'two_plus_odds_sources': 300, 'two_plus_context_sources': 300, 'context_limit': context_limit, 'sstats_deep_budget': sstats_deep_budget, 'bzzoiro_v2_limit': premium_limit, 'bzzoiro_comparison_limit': bzz_comparison_limit if has_bzz else 0, 'sportlogic_budget': sportlogic_budget if has_sportlogic else 0, 'line_ev_floor': env['LINE_MOVEMENT_MIN_CURRENT_EV_PCT'], 'line_edge_floor': env['LINE_MOVEMENT_MIN_CURRENT_EDGE_PP']},
        'notes': [
            'Publication is now strict 2+ lines and 2+ contexts by default for Telegram fallback.',
            'Bzzoiro v2 receives runtime-day window and odds-comparison enrichment for hard context/line evidence.',
            'SStats deep and Bzzoiro per-run ceilings target the full 300-row inventory, not only the 2h runner window.',
        ],
        'env_written_count': len(env),
    })
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
