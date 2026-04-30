from __future__ import annotations

"""Quality-first Telegram publication policy plus latest run blocker fixes.

This runtime layer is applied after provider-budget allocation in run-bot.yml, so
it is the safest place to repair late blockers without touching the scheduler.
It keeps publication quality strict, but fixes the data conditions that made the
latest run empty:
- odds-api.io must request all target books, not only Bet365/Unibet;
- weather must have shortlist fallback when venue/location is missing;
- api-football must not stay disabled by stale runtime config;
- B-tier must be usable for real moderate value, while weak 1-3% EV candidates
  remain rejected.
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
POLICY_VERSION = 'v5-quality-first-run-blocker-fixes'

SECRET_KEYS = {
    'API_FOOTBALL_KEY',
}


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
            value = '***' if key in SECRET_KEYS and env[key] else env[key]
            print(f'{key}={value}')


def first_non_empty_env(names: list[str]) -> str:
    for name in names:
        value = str(os.getenv(name) or '').strip()
        if value:
            return value
    return ''


def redacted_env(env: dict[str, str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for key, value in env.items():
        if key in SECRET_KEYS or any(token in key.upper() for token in ('TOKEN', 'SECRET', 'PASSWORD')):
            result[key] = '***' if value else ''
        else:
            result[key] = value
    return result


def main() -> int:
    env = {
        'QUALITY_FIRST_RUNTIME_POLICY_ACTIVE': 'true',
        'QUALITY_FIRST_RUNTIME_POLICY_VERSION': POLICY_VERSION,
        'RUN_BLOCKER_FIX_POLICY_ACTIVE': 'true',
        'RUN_BLOCKER_FIX_POLICY_VERSION': POLICY_VERSION,

        # Publication market policy.
        # A-tier stays conservative; B-tier can publish guarded totals/DNB/BTTS.
        'TELEGRAM_MARKETS': 'totals,dnb,btts',
        'CONTROLLED_FALLBACK_ALLOWED_FAMILIES': 'totals,dnb,btts',
        'CONTROLLED_FALLBACK_TIER_A_ALLOWED_FAMILIES': 'totals',
        'CONTROLLED_FALLBACK_TIER_B_ALLOWED_FAMILIES': 'totals,dnb,btts',
        'CONTROLLED_FALLBACK_TIER_C_ALLOWED_FAMILIES': '',
        'CONTROLLED_FALLBACK_TIER_C_PUBLISH_ENABLED': 'false',
        'DAILY_BEST5_ALLOWED_FAMILIES': 'totals,dnb,btts',
        'DAILY_BEST5_TIER_A_ALLOWED_FAMILIES': 'totals',
        'DAILY_BEST5_TIER_B_ALLOWED_FAMILIES': 'totals,dnb,btts',

        # Odds coverage: the latest run requested only Bet365/Unibet and produced
        # mostly single-book/single-source signals plus zero market-derived raw.
        'TARGET_BOOKMAKERS': 'Bet365,Unibet,Betfair Exchange,Sbobet',
        'CONSENSUS_BOOKMAKERS': 'Bet365,Unibet,Betfair Exchange,Sbobet',
        'SHARP_BOOKMAKERS': 'Bet365,Unibet,Betfair Exchange,Sbobet',
        'ODDS_API_IO_BOOKMAKERS': 'Bet365,Unibet,Betfair Exchange,Sbobet',
        'ODDS_API_IO_BOOKMAKERS_ACCOUNT1': 'Bet365,Unibet',
        'ODDS_API_IO_BOOKMAKERS_ACCOUNT2': 'Betfair Exchange,Sbobet',
        'ODDS_API_IO_ACCOUNT1_PER_RUN_MAX': '70',
        'ODDS_API_IO_ACCOUNT2_PER_RUN_MAX': '70',
        'ODDS_API_IO_PER_RUN_MAX': '140',
        'ODDS_API_IO_MAX_HTTP_REQUESTS_PER_RUN': '140',
        'ODDS_API_IO_PAGE_LIMIT': '100',
        'ODDS_API_IO_MAX_EVENT_PAGES_PER_SPORT': '24',
        'MAX_MATCHES_FOR_ODDS_FETCH': '180',
        'MIN_BOOKS_FOR_CONSENSUS': '2',
        'STRONG_MARKET_MIN_BOOKS': '2',

        # Candidate pool: widen internally, keep final publication gates strict.
        'MARKET_DERIVED_CANDIDATES_ENABLED': 'true',
        'MARKET_DERIVED_MIN_BOOKS': '2',
        'MARKET_DERIVED_MIN_SOURCES': '1',
        'MARKET_DERIVED_MIN_OBSERVATIONS': '1',
        'MARKET_DERIVED_MIN_EDGE_PCT': '1.0',
        'MARKET_DERIVED_MAX_DISPERSION_PCT': '8.0',
        'MARKET_DERIVED_CONSENSUS_RELIEF_ENABLED': 'true',
        'MARKET_DERIVED_CONSENSUS_RELIEF_MIN_BOOKS': '2',
        'MARKET_DERIVED_CONSENSUS_RELIEF_MIN_SOURCES': '1',
        'MARKET_DERIVED_CONSENSUS_RELIEF_MIN_OBSERVATIONS': '1',
        'MARKET_DERIVED_CONSENSUS_RELIEF_MIN_EDGE_PCT': '1.8',
        'MARKET_DERIVED_CONSENSUS_RELIEF_MAX_DISPERSION_PCT': '5.5',
        'MAX_CANDIDATES_PER_MATCH_PRE_FILTER': '5',
        'MAX_INTERNAL_CANDIDATES_PER_RUN': '30',
        'SHADOW_TRACKING_ENABLED': 'true',
        'SHADOW_TRACKING_MAX_PER_RUN': '20',
        'SHADOW_TRACKING_STORE_QUALITY_REJECTIONS': 'true',

        # Odds bounds.
        'ODDS_MIN': '1.45',
        'ODDS_MAX': '2.25',
        'TARGET_ODDS_HARD_MIN': '1.45',
        'TARGET_ODDS_HARD_MAX': '2.25',
        'CONTROLLED_FALLBACK_GLOBAL_MIN_ODDS': '1.45',
        'CONTROLLED_FALLBACK_GLOBAL_MAX_ODDS': '2.25',
        'CONTROLLED_FALLBACK_REQUIRE_2_BOOKS_FOR_TELEGRAM': 'true',
        'CONTROLLED_FALLBACK_REJECT_PROXY_SINGLE_BOOK': 'true',
        'CONTROLLED_FALLBACK_REQUIRE_MARKET_CONFIRMATION_FOR_PROXY': 'true',
        'CONTROLLED_FALLBACK_PROXY_SINGLE_SOURCE_STRICT': 'true',

        # Tier A: strong totals only.
        'CONTROLLED_FALLBACK_TIER_A_MIN_BOOKS': '2',
        'CONTROLLED_FALLBACK_TIER_A_MIN_CONFIDENCE': '65.0',
        'CONTROLLED_FALLBACK_TIER_A_MIN_QUALITY': '65.0',
        'CONTROLLED_FALLBACK_TIER_A_MIN_EDGE_PP': '3.5',
        'CONTROLLED_FALLBACK_TIER_A_MIN_EV_PCT': '7.0',
        'CONTROLLED_FALLBACK_TIER_A_MIN_PUBLICATION_SCORE': '18',
        'CONTROLLED_FALLBACK_TIER_A_MAX_ODDS': '2.20',

        # Tier B: usable but still quality-first. This rejects the latest weak
        # near-misses (EV 1-3%, edge 0.5-1.4pp), but admits real moderate value.
        'CONTROLLED_FALLBACK_TIER_B_MIN_BOOKS': '2',
        'CONTROLLED_FALLBACK_TIER_B_MIN_CONFIDENCE': '62.0',
        'CONTROLLED_FALLBACK_TIER_B_MIN_QUALITY': '61.5',
        'CONTROLLED_FALLBACK_TIER_B_MIN_EDGE_PP': '2.6',
        'CONTROLLED_FALLBACK_TIER_B_MIN_EV_PCT': '5.5',
        'CONTROLLED_FALLBACK_TIER_B_MIN_PUBLICATION_SCORE': '13',
        'CONTROLLED_FALLBACK_TIER_B_MAX_ODDS': '2.25',

        # Explicit totals B-grace markers for reports/future code paths.
        'CONTROLLED_FALLBACK_TOTALS_B_GRACE_ENABLED': 'true',
        'CONTROLLED_FALLBACK_TOTALS_B_GRACE_MIN_BOOKS': '2',
        'CONTROLLED_FALLBACK_TOTALS_B_GRACE_MIN_CONFIDENCE': '62.0',
        'CONTROLLED_FALLBACK_TOTALS_B_GRACE_MIN_QUALITY': '61.5',
        'CONTROLLED_FALLBACK_TOTALS_B_GRACE_MIN_EDGE_PP': '2.6',
        'CONTROLLED_FALLBACK_TOTALS_B_GRACE_MIN_EV_PCT': '5.5',
        'CONTROLLED_FALLBACK_TOTALS_B_GRACE_MAX_ODDS': '2.25',
        'CONTROLLED_FALLBACK_TOTALS_B_GRACE_MAX_XG_GAP_PP': '12.0',

        # Final value gates follow the B-tier standard.
        'CONTROLLED_FALLBACK_FINAL_MIN_EDGE_PP': '2.6',
        'CONTROLLED_FALLBACK_FINAL_MIN_EV_PCT': '5.5',
        'CONTROLLED_FALLBACK_EXTRA_PICK_MIN_EDGE_PP': '2.8',
        'CONTROLLED_FALLBACK_EXTRA_PICK_MIN_EV_PCT': '6.0',
        'CONTROLLED_FALLBACK_EXTRA_PICK_MIN_CONFIDENCE': '62.0',
        'CONTROLLED_FALLBACK_PROXY_SINGLE_SOURCE_MIN_CONFIDENCE': '64.0',
        'CONTROLLED_FALLBACK_PROXY_SINGLE_SOURCE_MIN_EDGE_PP': '4.0',
        'CONTROLLED_FALLBACK_PROXY_SINGLE_SOURCE_MIN_EV_PCT': '7.5',

        # Sanity guards: less brittle than the previous 12pp/0.18 setup, but still
        # rejects obvious xG/model contradictions.
        'CONTROLLED_FALLBACK_XG_SANITY_ENABLED': 'true',
        'CONTROLLED_FALLBACK_REQUIRE_TOTALS_SANITY_FOR_TELEGRAM': 'true',
        'CONTROLLED_FALLBACK_XG_DIRECTION_MARGIN': '0.25',
        'CONTROLLED_FALLBACK_XG_HARD_REJECT_GAP_PP': '14.0',
        'CONTROLLED_FALLBACK_TIER_A_REQUIRE_XG_SANITY': 'true',
        'CONTROLLED_FALLBACK_TIER_A_MAX_XG_GAP_PP': '7.0',
        'CONTROLLED_FALLBACK_TIER_B_MAX_XG_GAP_PP': '12.0',

        # DNB sanity: no-push xG probability must not be materially worse than model.
        'CONTROLLED_FALLBACK_DNB_SANITY_ENABLED': 'true',
        'CONTROLLED_FALLBACK_DNB_MAX_MODEL_OPTIMISM_GAP_PP': '10.0',
        'CONTROLLED_FALLBACK_DNB_HARD_REJECT_GAP_PP': '13.0',

        # BTTS sanity: requires xG agreement and blocks obvious low/high-team-xG conflicts.
        'CONTROLLED_FALLBACK_BTTS_SANITY_ENABLED': 'true',
        'CONTROLLED_FALLBACK_BTTS_LOW_TEAM_XG_THRESHOLD': '0.72',
        'CONTROLLED_FALLBACK_BTTS_HIGH_TEAM_XG_THRESHOLD': '1.15',
        'CONTROLLED_FALLBACK_BTTS_HARD_REJECT_GAP_PP': '13.0',

        # Weather should be spent on useful shortlist candidates. The latest run had
        # budget but zero calls because location was missing for all 58 contexts.
        'WEATHER_CONTEXT_ENABLED': 'true',
        'WEATHER_SHORTLIST_ONLY': 'true',
        'WEATHER_ALLOW_TEAM_NAME_FALLBACK': 'true',
        'WEATHER_CONTEXT_MATCH_LIMIT': '16',
        'WEATHERAPI_PER_RUN_MAX': '12',
        'WEATHERAPI_MAX_HTTP_REQUESTS_PER_RUN': '12',
        'OPENWEATHERMAP_PER_RUN_MAX': '8',
        'OPENWEATHERMAP_MAX_HTTP_REQUESTS_PER_RUN': '8',
        'WEATHER_OPENWEATHERMAP_FALLBACK_ENABLED': 'true',
        'WEATHER_CACHE_TTL_MINUTES': '240',

        # Re-enable api-football with a small cap after the old provider-budget layer
        # blanks it. The key is restored below only if one is available in env.
        'ENABLE_API_FOOTBALL': 'true',
        'API_FOOTBALL_ENABLED': 'true',
        'API_FOOTBALL_PER_RUN_MAX': '8',
        'API_FOOTBALL_MAX_HTTP_REQUESTS_PER_RUN': '8',
        'API_FOOTBALL_CONTEXT_MATCH_LIMIT': '36',
        'API_FOOTBALL_PREDICTIONS_LIMIT': '12',
        'API_FOOTBALL_REQUEST_BUDGET_GRANTED': '8',
        'API_FOOTBALL_REQUEST_BUDGET_REASON': 'reenabled_by_quality_first_policy',
        'API_FOOTBALL_AUTH_ERROR_COOLDOWN_MINUTES': '1440',
        'API_FOOTBALL_RATE_LIMIT_COOLDOWN_MINUTES': '180',

        # RapidAPI discovery/probe is diagnostic only.
        'RAPIDAPI_PROBE_ENABLED': 'false',
        'RAPIDAPI_ENDPOINT_DISCOVERY_ENABLED': 'false',
        'RAPIDAPI_SPORTSBOOK_PROBE_ENABLED': 'false',
        'RAPIDAPI_FREE_FOOTBALL_PROBE_ENABLED': 'false',
        'RAPIDAPI_SPORTAPI7_PROBE_ENABLED': 'false',

        # OddsPapi is routed through RapidAPI.
        'ODDSPAPI_RAPIDAPI_ENABLED': 'true',
        'ODDSPAPI_RAPIDAPI_HOST': 'odds-api1.p.rapidapi.com',
        'ODDSPAPI_BASE_URL': 'https://odds-api1.p.rapidapi.com/v4',
        'ODDSPAPI_RAPIDAPI_USE_QUERY_API_KEY': 'false',

        # Target-5 cadence, no forced low-quality volume.
        'DAILY_TOP5_TARGET_PICKS': os.getenv('DAILY_TOP5_TARGET_PICKS', '5'),
        'DAILY_BEST5_TARGET_PICKS': os.getenv('DAILY_BEST5_TARGET_PICKS', '5'),
        'DAILY_TOP5_NO_HARD_CAP': 'true',
        'VOLUME_NO_DAILY_HARD_CAP': 'true',
        'CONTROLLED_FALLBACK_MAX_PICKS_PER_RUN': os.getenv('CONTROLLED_FALLBACK_MAX_PICKS_PER_RUN', '2'),
        'CONTROLLED_FALLBACK_ABSOLUTE_MAX_PICKS_PER_RUN': os.getenv('CONTROLLED_FALLBACK_ABSOLUTE_MAX_PICKS_PER_RUN', '2'),
        'MAX_PICKS_PER_RUN': os.getenv('MAX_PICKS_PER_RUN', '2'),
        'PUBLISH_WINDOW_HOURS': '12',
        'MIN_KICKOFF_LEAD_MINUTES': '25',

        # Bankroll: lower flat exposure while collecting a clean post-policy sample.
        'BANKROLL_KELLY_ENABLED': 'false',
        'BANKROLL_FLAT_STAKE_PCT': '0.50',
        'BANKROLL_MAX_STAKE_PCT': '0.75',
        'BANKROLL_MAX_OPEN_EXPOSURE_PCT': '10',
        'CONTROLLED_FALLBACK_TOTAL_STAKE_CAP_PCT': '2.50',
    }

    api_football_key = first_non_empty_env([
        'API_FOOTBALL_KEY',
        'APISPORTS_KEY',
        'API_SPORTS_KEY',
        'SPORTAPI_API_KEY',
        'SPORTAPI7_RAPIDAPI_KEY',
        'RAPIDAPI_KEY',
    ])
    if api_football_key:
        env['API_FOOTBALL_KEY'] = api_football_key

    append_env(env)

    report = {
        'status': 'ok',
        'version': POLICY_VERSION,
        'updated_at_utc': datetime.now(UTC).isoformat(),
        'applied_env': redacted_env(env),
        'summary': {
            'telegram_publication': 'tier_a_totals_only__tier_b_totals_dnb_btts',
            'diagnostics': 'all generated candidates can still be tracked in reports/shadow',
            'target': 'about 3-5 best picks/day when value exists, no forced volume',
            'odds_fix': 'four target bookmakers requested to reduce single-book and revive market-derived candidates',
            'weather_fix': 'shortlist fallback enabled so missing venue/location no longer forces 0 weather calls',
            'api_football_fix': 'provider re-enabled after budget layer; key is restored only if available from env/secrets',
            'b_tier': '2 books, EV >= 5.5%, edge >= 2.6pp, odds <= 2.25, sanity guards enabled',
            'latest_near_misses': 'EV 1-3% and edge below 1.5pp remain blocked',
        },
    }
    write_json(OUT, report)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
