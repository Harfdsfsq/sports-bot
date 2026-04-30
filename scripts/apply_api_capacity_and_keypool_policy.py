from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path('.').resolve()
UTC = timezone.utc
GITHUB_ENV = os.getenv('GITHUB_ENV')
POLICY_PATH = ROOT / 'config' / 'provider_request_budget.json'
OUT = ROOT / '.data' / 'exports' / 'latest-api-capacity-keypool-policy.json'
POLICY_VERSION = 'v3-capacity-keypool-full-day-enrichment-no-api-football'

KEY_POOL_ENV = {
    'ODDS_API_IO_KEY_POOL': ['ODDS_API_IO_KEY', 'ODDS_API_IO_KEY_2', 'ODDS_API_IO_KEY_3'],
    'SSTATS_API_KEY_POOL': ['SSTATS_API_KEY', 'SSTATS_API_KEY_2', 'SSTATS_API_KEY_3'],
    'WEATHERAPI_KEY_POOL': ['WEATHERAPI_KEY', 'WEATHERAPI_KEY_2'],
    'OPENWEATHERMAP_KEY_POOL': ['OPENWEATHERMAP_API_KEY', 'OPENWEATHERMAP_KEY', 'OPENWEATHER_API_KEY'],
    'NEWSDATA_KEY_POOL': ['NEWSDATA_API_KEY', 'NEWSDATA_API_KEY_2'],
    'GUARDIAN_KEY_POOL': ['GUARDIAN_API_KEY', 'GUARDIAN_OPEN_PLATFORM_KEY'],
    'HIGHLIGHTLY_KEY_POOL': ['HIGHLIGHTLY_API_KEY', 'HIGHLIGHTLY_RAPIDAPI_KEY'],
}

TARGET_BOOKMAKERS = 'Bet365,Unibet,Betfair Exchange,Sbobet'

PROVIDER_PATCHES: dict[str, dict[str, Any]] = {
    'odds_api_io': {
        'enabled': True,
        'per_run_max': 140,
        'limit': {
            'requests_per_hour_per_account': 100,
            'budget_scope': 'per_run_dual_account',
            'bookmakers_per_account': 2,
            'account1_bookmakers': ['Bet365', 'Unibet'],
            'account2_bookmakers': ['Betfair Exchange', 'Sbobet'],
        },
        'env': {
            'ENABLE_ODDS_API_IO': 'true',
            'ODDS_API_IO_ENABLED': 'true',
            # Global cap across both accounts. Per-account caps below prevent one key from burning all quota.
            'ODDS_API_IO_PER_RUN_MAX': os.getenv('ODDS_API_IO_PER_RUN_MAX', '200'),
            'ODDS_API_IO_MAX_HTTP_REQUESTS_PER_RUN': os.getenv('ODDS_API_IO_MAX_HTTP_REQUESTS_PER_RUN', '200'),
            'ODDS_API_IO_ACCOUNT1_PER_RUN_MAX': os.getenv('ODDS_API_IO_ACCOUNT1_PER_RUN_MAX', '100'),
            'ODDS_API_IO_ACCOUNT2_PER_RUN_MAX': os.getenv('ODDS_API_IO_ACCOUNT2_PER_RUN_MAX', '100'),
            'ODDS_API_IO_PAGE_LIMIT': '100',
            'ODDS_API_IO_MAX_EVENT_PAGES_PER_SPORT': '24',
            'MAX_MATCHES_FOR_ODDS_FETCH': '520',
            # Critical: keep the global bookmaker list at four books. The previous two-book value
            # made many candidates single-book/single-source and killed market-derived signals.
            'ODDS_API_IO_BOOKMAKERS': TARGET_BOOKMAKERS,
            'TARGET_BOOKMAKERS': TARGET_BOOKMAKERS,
            'CONSENSUS_BOOKMAKERS': TARGET_BOOKMAKERS,
            'SHARP_BOOKMAKERS': TARGET_BOOKMAKERS,
            'ODDS_API_IO_BOOKMAKERS_ACCOUNT1': os.getenv('ODDS_API_IO_BOOKMAKERS_ACCOUNT1', 'Bet365,Unibet'),
            'ODDS_API_IO_BOOKMAKERS_ACCOUNT2': os.getenv('ODDS_API_IO_BOOKMAKERS_ACCOUNT2', 'Betfair Exchange,Sbobet'),
        },
        'disable_env': {'ODDS_API_IO_PER_RUN_MAX': '0', 'ODDS_API_IO_MAX_HTTP_REQUESTS_PER_RUN': '0', 'MAX_MATCHES_FOR_ODDS_FETCH': '0'},
    },
    'sstats': {
        'enabled': True,
        'per_run_max': 150,
        'limit': {'requests_per_minute': 150, 'budget_scope': 'per_run'},
        'env': {
            'ENABLE_SSTATS': 'true',
            'ENABLE_SSTATS_CONTEXT': 'true',
            'SSTATS_ENABLED': 'true',
            'SSTATS_PER_RUN_MAX': '150',
            'SSTATS_REQUESTS_MAX_PER_RUN': '150',
            'SSTATS_MAX_HTTP_REQUESTS_PER_RUN': '150',
            'SSTATS_CONTEXT_MATCH_LIMIT': '320',
            'SSTATS_LOOKBACK_DAYS': '45',
            'SSTATS_RECENT_MATCHES': '12',
            'SSTATS_REQUEST_CHUNK_DAYS': '5',
        },
        'disable_env': {'SSTATS_PER_RUN_MAX': '0', 'SSTATS_REQUESTS_MAX_PER_RUN': '0', 'SSTATS_CONTEXT_MATCH_LIMIT': '0'},
    },
    'external_signals': {
        'enabled': True,
        'per_run_max': 120,
        'limit': {'mixed_free_context_sources': True, 'budget_scope': 'per_run'},
        'secret_env_keys': ['NEWSDATA_API_KEY', 'GUARDIAN_API_KEY', 'HIGHLIGHTLY_API_KEY'],
        'env': {
            'ENABLE_EXTERNAL_SIGNALS': 'true',
            'EXTERNAL_SIGNALS_PER_RUN_MAX': '120',
            'EXTERNAL_SIGNALS_CONTEXT_MATCH_LIMIT': '180',
            'ENABLE_CLUBELO_CONTEXT': 'true',
            'ENABLE_FOOTBALL_DATA_UK_CONTEXT': 'true',
            'ENABLE_OPEN_METEO_CONTEXT': 'true',
            'ENABLE_WIKIDATA_CONTEXT': 'true',
            'ENABLE_NEWSDATA_CONTEXT': 'true',
            'ENABLE_GUARDIAN_CONTEXT': 'true',
            'ENABLE_HIGHLIGHTLY_CONTEXT': 'true',
            'FOOTBALL_DATA_UK_LEAGUE_CODES': os.getenv('FOOTBALL_DATA_UK_LEAGUE_CODES', 'E0,E1,D1,D2,I1,I2,SP1,SP2,F1,F2,N1,P1,B1,SC0,T1'),
            'FOOTBALL_DATA_UK_MAX_LEAGUES_PER_RUN': '14',
            'FOOTBALL_DATA_UK_CACHE_TTL_HOURS': '12',
            'CLUBELO_CACHE_TTL_HOURS': '72',
            'OPEN_METEO_CACHE_TTL_HOURS': '6',
            'WIKIDATA_CACHE_TTL_HOURS': '168',
        },
        'disable_env': {'ENABLE_EXTERNAL_SIGNALS': 'false', 'EXTERNAL_SIGNALS_PER_RUN_MAX': '0', 'EXTERNAL_SIGNALS_CONTEXT_MATCH_LIMIT': '0'},
    },
    'weatherapi': {
        'enabled': True,
        'per_run_max': 80,
        'env': {
            'WEATHERAPI_PER_RUN_MAX': '80',
            'WEATHERAPI_MAX_HTTP_REQUESTS_PER_RUN': '80',
            'WEATHER_CONTEXT_MATCH_LIMIT': '140',
            'WEATHER_SHORTLIST_ONLY': 'true',
            'WEATHER_ALLOW_TEAM_NAME_FALLBACK': 'true',
            'WEATHER_CACHE_TTL_MINUTES': '240',
        },
    },
    'openweathermap': {
        'enabled': True,
        'per_run_max': 40,
        'env': {
            'OPENWEATHERMAP_PER_RUN_MAX': '40',
            'OPENWEATHERMAP_MAX_HTTP_REQUESTS_PER_RUN': '40',
            'WEATHER_OPENWEATHERMAP_FALLBACK_ENABLED': 'true',
        },
    },
    'football_data': {'enabled': True, 'per_run_max': 16, 'env': {'FOOTBALL_DATA_PER_RUN_MAX': '16', 'FOOTBALL_DATA_REQUESTS_MAX_PER_RUN': '16', 'FOOTBALL_DATA_CONTEXT_MATCH_LIMIT': '220'}},
    'thesportsdb': {'enabled': True, 'per_run_max': 30, 'env': {'THESPORTSDB_PER_RUN_MAX': '30', 'THESPORTSDB_REQUESTS_MAX_PER_RUN': '30', 'THESPORTSDB_CONTEXT_MATCH_LIMIT': '220'}},
}


def load_json(path: Path, default: Any) -> Any:
    try:
        if not path.exists():
            return default
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return default


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


def merge_provider(current: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    out = dict(current)
    for key, value in patch.items():
        if key in {'env', 'disable_env', 'limit'} and isinstance(value, dict):
            merged = dict(out.get(key) or {})
            merged.update({str(k): str(v) if key != 'limit' else v for k, v in value.items()})
            out[key] = merged
        else:
            out[key] = value
    for field in ('safe_daily_budget', 'safe_monthly_budget', 'min_spacing_minutes', 'allowed_msk_hours', 'manual_per_run_max'):
        out.pop(field, None)
    return out


def key_pool_value(names: list[str]) -> str:
    values: list[str] = []
    for name in names:
        raw = os.getenv(name)
        if raw and raw.strip() and raw.strip() not in values:
            values.append(raw.strip())
    return ','.join(values)


def main() -> int:
    policy = load_json(POLICY_PATH, {})
    if not isinstance(policy, dict):
        policy = {}
    providers = policy.get('providers') if isinstance(policy.get('providers'), dict) else {}
    providers = dict(providers)
    changed: dict[str, Any] = {}
    for name, patch in PROVIDER_PATCHES.items():
        current = providers.get(name) if isinstance(providers.get(name), dict) else {}
        providers[name] = merge_provider(current, patch)
        changed[name] = {'per_run_max': providers[name].get('per_run_max'), 'env': providers[name].get('env')}
    policy['providers'] = providers
    policy['version'] = POLICY_VERSION
    policy['description'] = 'Per-run capacity policy with full-day enrichment, dual odds-api.io account support, and api-football removed.'
    notes = list(policy.get('notes') or []) if isinstance(policy.get('notes'), list) else []
    notes.append('odds-api.io dual account mode: account1 Bet365/Unibet, account2 Betfair Exchange/Sbobet, max 2 bookmakers per account.')
    notes.append('Global ODDS_API_IO_BOOKMAKERS now stays at four books so consensus and market-derived signals can be formed.')
    notes.append('Weather and external-signals caps were raised because the latest run exhausted 40+24 weather and 60 external signal requests before covering all enriched matches.')
    notes.append('api-football remains deleted from production runtime and is not patched back by this layer.')
    policy['notes'] = notes
    write_json(POLICY_PATH, policy)

    env: dict[str, str] = {
        'API_CAPACITY_KEYPOOL_POLICY_ACTIVE': 'true',
        'API_CAPACITY_KEYPOOL_POLICY_VERSION': POLICY_VERSION,
        'ALL_SOURCES_FREE_MAXIMIZE': 'false',
        'ODDS_API_IO_PER_RUN_MAX': os.getenv('ODDS_API_IO_PER_RUN_MAX', '200'),
        'ODDS_API_IO_MAX_HTTP_REQUESTS_PER_RUN': os.getenv('ODDS_API_IO_MAX_HTTP_REQUESTS_PER_RUN', '200'),
        'ODDS_API_IO_ACCOUNT1_PER_RUN_MAX': os.getenv('ODDS_API_IO_ACCOUNT1_PER_RUN_MAX', '100'),
        'ODDS_API_IO_ACCOUNT2_PER_RUN_MAX': os.getenv('ODDS_API_IO_ACCOUNT2_PER_RUN_MAX', '100'),
        'ODDS_API_IO_BOOKMAKERS': TARGET_BOOKMAKERS,
        'TARGET_BOOKMAKERS': TARGET_BOOKMAKERS,
        'CONSENSUS_BOOKMAKERS': TARGET_BOOKMAKERS,
        'SHARP_BOOKMAKERS': TARGET_BOOKMAKERS,
        'ODDS_API_IO_BOOKMAKERS_ACCOUNT1': os.getenv('ODDS_API_IO_BOOKMAKERS_ACCOUNT1', 'Bet365,Unibet'),
        'ODDS_API_IO_BOOKMAKERS_ACCOUNT2': os.getenv('ODDS_API_IO_BOOKMAKERS_ACCOUNT2', 'Betfair Exchange,Sbobet'),
        'MAX_MATCHES_FOR_ODDS_FETCH': '520',
        'SSTATS_PER_RUN_MAX': '150',
        'SSTATS_REQUESTS_MAX_PER_RUN': '150',
        'SSTATS_MAX_HTTP_REQUESTS_PER_RUN': '150',
        'SSTATS_CONTEXT_MATCH_LIMIT': '320',
        'ENABLE_EXTERNAL_SIGNALS': 'true',
        'EXTERNAL_SIGNALS_PER_RUN_MAX': '120',
        'EXTERNAL_SIGNALS_CONTEXT_MATCH_LIMIT': '180',
        'WEATHERAPI_PER_RUN_MAX': '80',
        'WEATHERAPI_MAX_HTTP_REQUESTS_PER_RUN': '80',
        'OPENWEATHERMAP_PER_RUN_MAX': '40',
        'OPENWEATHERMAP_MAX_HTTP_REQUESTS_PER_RUN': '40',
        'WEATHER_CONTEXT_MATCH_LIMIT': '140',
        'WEATHER_SHORTLIST_ONLY': 'true',
        'WEATHER_ALLOW_TEAM_NAME_FALLBACK': 'true',
    }
    for pool_name, secret_names in KEY_POOL_ENV.items():
        value = key_pool_value(secret_names)
        if value:
            env[pool_name] = value
            env[f'{pool_name}_SIZE'] = str(len(value.split(',')))
    append_env(env)

    report = {
        'status': 'ok',
        'version': POLICY_VERSION,
        'updated_at_utc': datetime.now(UTC).isoformat(),
        'changed': changed,
        'key_pool_secret_names': KEY_POOL_ENV,
        'applied_env_public': {k: ('***' if k.endswith('_POOL') else v) for k, v in env.items()},
        'notes': policy['notes'][-4:],
    }
    write_json(OUT, report)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
