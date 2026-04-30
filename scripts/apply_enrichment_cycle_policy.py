from __future__ import annotations

"""Full-day enrichment cycle policy.

Order enforced by env for the next runner steps:
1) build/refresh the full-day fixture inventory;
2) fetch odds for as many day fixtures as possible;
3) spend context/weather/news budgets on odds-backed matches first;
4) merge coverage back into day inventory;
5) repeat on the next two-hour run until gaps close.
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path('.').resolve()
OUT = ROOT / '.data' / 'exports' / 'latest-enrichment-cycle-policy.json'
GITHUB_ENV = os.getenv('GITHUB_ENV')
UTC = timezone.utc
POLICY_VERSION = 'v1-full-day-fixtures-odds-context-loop'


def tzinfo() -> ZoneInfo:
    try:
        return ZoneInfo(os.getenv('APP_TIMEZONE') or os.getenv('TZ') or 'Europe/Moscow')
    except Exception:
        return ZoneInfo('Europe/Moscow')


def load_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def as_int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ''):
            return default
        return int(float(value))
    except Exception:
        return default


def count_flag(rows: list[Any], flag: str) -> int:
    total = 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        coverage = row.get('coverage') if isinstance(row.get('coverage'), dict) else {}
        if bool(coverage.get(flag)):
            total += 1
    return total


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


def ratio(part: int, total: int) -> float:
    return round((part / total * 100.0), 2) if total > 0 else 0.0


def main() -> int:
    now_utc = datetime.now(UTC)
    local_now = now_utc.astimezone(tzinfo())
    target_date = os.getenv('DAY_INVENTORY_TARGET_DATE') or local_now.date().isoformat()
    inventory_path = ROOT / '.data' / 'day_inventory' / f'{target_date}.json'
    inventory = load_json(inventory_path)
    rows = inventory.get('matches') if isinstance(inventory.get('matches'), list) else []
    counts = inventory.get('counts') if isinstance(inventory.get('counts'), dict) else {}

    matches_total = as_int(counts.get('matches_total'), len(rows))
    with_odds = as_int(counts.get('matches_with_odds'), 0) or count_flag(rows, 'odds')
    with_context = as_int(counts.get('matches_with_context'), 0) or count_flag(rows, 'context')
    ready = as_int(counts.get('matches_ready_for_model'), 0) or count_flag(rows, 'ready_for_model')
    with_weather = as_int(counts.get('matches_with_weather'), 0) or count_flag(rows, 'weather')

    missing_odds = max(0, matches_total - with_odds)
    missing_context = max(0, with_odds - with_context)
    missing_ready = max(0, with_odds - ready)
    missing_weather = max(0, with_context - with_weather)
    needs_backfill = (
        matches_total == 0
        or missing_odds > 0
        or missing_context > 0
        or missing_ready > 0
        or missing_weather > 0
        or ratio(ready, max(1, with_odds)) < 90.0
    )

    # Critical fix: a stale COVERAGE_MAXIMIZE_UNTIL_LOCAL_DATE disabled coverage mode on 30.04.
    # Blank it and make today's enrichment cycle permanent while this script is active.
    env = {
        'ENRICHMENT_CYCLE_POLICY_ACTIVE': 'true',
        'ENRICHMENT_CYCLE_POLICY_VERSION': POLICY_VERSION,
        'COVERAGE_MAXIMIZE_TODAY': 'true',
        'COVERAGE_MAXIMIZE_UNTIL_LOCAL_DATE': '',
        'DAY_INVENTORY_TARGET_DATE': target_date,
        'DAY_INVENTORY_MIN_READY_RATIO_PCT': '88',
        'DAY_INVENTORY_MIN_MATCHES_FOR_SKIP': '120',
        'DAY_INVENTORY_REFRESH_INTERVAL_HOURS': '2',
        'DAY_INVENTORY_FORCE_PROVIDER_MERGE': 'true',
        'DAY_INVENTORY_COVERAGE_MAX_REBUILD': 'true',
        'DAY_INVENTORY_BOOTSTRAP_PROVIDER': 'odds_api_io',
        'MATCH_BOOTSTRAP_PROVIDER': 'odds_api_io',
        'FULL_DAY_ENRICHMENT_CYCLE_ACTIVE': 'true',
        'PUBLISH_WINDOW_HOURS': '30',
        'ANALYSIS_MATCH_CAP_PER_RUN': '360',
        'MAX_MATCHES_FOR_ODDS_FETCH': '320',
        'ODDS_API_IO_PER_RUN_MAX': '140',
        'ODDS_API_IO_MAX_HTTP_REQUESTS_PER_RUN': '140',
        'ODDS_API_IO_BOOKMAKERS': 'Bet365,Unibet,Betfair Exchange,Sbobet',
        'TARGET_BOOKMAKERS': 'Bet365,Unibet,Betfair Exchange,Sbobet',
        'CONSENSUS_BOOKMAKERS': 'Bet365,Unibet,Betfair Exchange,Sbobet',
        'SHARP_BOOKMAKERS': 'Bet365,Unibet,Betfair Exchange,Sbobet',
        # Odds first, then context. This stops context APIs from spending quota on fixtures
        # that still have no usable line.
        'CONTEXT_ENRICHMENT_REQUIRES_OFFERS': 'true',
        'CONTEXT_ENRICHMENT_MATCH_LIMIT': '360',
        'PREMIUM_CONTEXT_SHORTLIST_LIMIT': '180',
        'BZZOIRO_CONTEXT_MATCH_LIMIT': '320',
        'SSTATS_CONTEXT_MATCH_LIMIT': '260',
        'SSTATS_REQUESTS_MAX_PER_RUN': '150',
        'THESPORTSDB_CONTEXT_MATCH_LIMIT': '220',
        'FOOTBALL_DATA_CONTEXT_MATCH_LIMIT': '220',
        'FUTRIXMETRICS_CONTEXT_MATCH_LIMIT': '80',
        'NEWS_CONTEXT_MATCH_LIMIT': '12',
        'GNEWS_CONTEXT_MATCH_LIMIT': '8',
        'WEATHER_CONTEXT_ENABLED': 'true',
        'WEATHER_CONTEXT_MATCH_LIMIT': '140',
        'WEATHER_SHORTLIST_ONLY': 'true',
        'WEATHER_ALLOW_TEAM_NAME_FALLBACK': 'true',
        'WEATHERAPI_PER_RUN_MAX': '80',
        'WEATHERAPI_MAX_HTTP_REQUESTS_PER_RUN': '80',
        'OPENWEATHERMAP_PER_RUN_MAX': '40',
        'OPENWEATHERMAP_MAX_HTTP_REQUESTS_PER_RUN': '40',
        'MARKET_DERIVED_CANDIDATES_ENABLED': 'true',
        'MARKET_DERIVED_MIN_BOOKS': '2',
        'MARKET_DERIVED_MIN_OBSERVATIONS': '1',
        'ENABLE_API_FOOTBALL': 'false',
        'API_FOOTBALL_ENABLED': 'false',
        'API_FOOTBALL_KEY': '',
        'API_FOOTBALL_PER_RUN_MAX': '0',
        'API_FOOTBALL_CONTEXT_MATCH_LIMIT': '0',
        'API_FOOTBALL_PREDICTIONS_LIMIT': '0',
    }
    if needs_backfill:
        env['DAY_INVENTORY_FORCE_REFRESH'] = 'true'

    append_env(env)
    report = {
        'status': 'ok',
        'version': POLICY_VERSION,
        'updated_at_utc': now_utc.isoformat(),
        'target_date': target_date,
        'inventory_exists': inventory_path.exists(),
        'counts': {
            'matches_total': matches_total,
            'matches_with_odds': with_odds,
            'matches_with_context': with_context,
            'matches_ready_for_model': ready,
            'matches_with_weather': with_weather,
        },
        'gaps': {
            'missing_odds_from_day_inventory': missing_odds,
            'missing_context_for_odds_matches': missing_context,
            'missing_ready_for_odds_matches': missing_ready,
            'missing_weather_for_context_matches': missing_weather,
        },
        'ratios_pct': {
            'odds_over_total': ratio(with_odds, matches_total),
            'context_over_odds': ratio(with_context, max(1, with_odds)),
            'ready_over_odds': ratio(ready, max(1, with_odds)),
        },
        'needs_backfill': needs_backfill,
        'loop_order': ['fixtures', 'odds', 'contexts', 'weather/news/xG/form', 'merge coverage', 'repeat next run'],
        'env_written_count': len(env),
    }
    write_json(OUT, report)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
