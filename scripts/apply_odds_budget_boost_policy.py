from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path('.').resolve()
POLICY_PATH = ROOT / 'config' / 'provider_request_budget.json'
OUT = ROOT / '.data' / 'exports' / 'latest-odds-budget-boost-policy.json'
GITHUB_ENV = os.getenv('GITHUB_ENV')
UTC = timezone.utc


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return default


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')


def append_env(env: dict[str, str]) -> None:
    if not GITHUB_ENV:
        for key in sorted(env):
            print(f'{key}={env[key]}')
        return
    with open(GITHUB_ENV, 'a', encoding='utf-8') as fh:
        for key in sorted(env):
            fh.write(f'{key}={env[key]}\n')


def patch_provider(providers: dict[str, Any], name: str, patch: dict[str, Any]) -> dict[str, Any]:
    row = providers.get(name)
    if not isinstance(row, dict):
        row = {}
        providers[name] = row
    for key, value in patch.items():
        if key == 'env':
            env = dict(row.get('env') or {})
            env.update({str(k): str(v) for k, v in dict(value).items()})
            row['env'] = env
        elif isinstance(value, dict) and isinstance(row.get(key), dict):
            nested = dict(row.get(key) or {})
            nested.update(value)
            row[key] = nested
        else:
            row[key] = value
    return row


def main() -> int:
    policy = load_json(POLICY_PATH, {})
    if not isinstance(policy, dict):
        policy = {}
    providers = policy.setdefault('providers', {})
    if not isinstance(providers, dict):
        providers = {}
        policy['providers'] = providers

    odds_global = os.getenv('ODDS_API_IO_PER_RUN_MAX', '200')
    odds_account1 = os.getenv('ODDS_API_IO_ACCOUNT1_PER_RUN_MAX', '100')
    odds_account2 = os.getenv('ODDS_API_IO_ACCOUNT2_PER_RUN_MAX', '100')

    # RULES.txt: each odds-api.io free account allows 100 requests/hour and 2 selected bookmakers.
    # Dual-account mode keeps 2 books per account, not 4 books on one key.
    odds = patch_provider(providers, 'odds_api_io', {
        'per_run_max': int(float(odds_global or 140)),
        'min_spacing_minutes': 0,
        'limit': {
            'requests_per_hour_per_account': 100,
            'bookmakers_per_account': 2,
            'budget_scope': 'per_run_dual_account',
            'account1_bookmakers': ['Bet365', 'Unibet'],
            'account2_bookmakers': ['Betfair Exchange', 'Sbobet'],
            'rules_source': 'RULES.txt + dual-account user configuration',
        },
        'env': {
            'ENABLE_ODDS_API_IO': 'true',
            'ODDS_API_IO_ENABLED': 'true',
            'ODDS_API_IO_PER_RUN_MAX': odds_global,
            'ODDS_API_IO_MAX_HTTP_REQUESTS_PER_RUN': odds_global,
            'ODDS_API_IO_ACCOUNT1_PER_RUN_MAX': odds_account1,
            'ODDS_API_IO_ACCOUNT2_PER_RUN_MAX': odds_account2,
            'ODDS_API_IO_BOOKMAKERS': 'Bet365,Unibet',
            'ODDS_API_IO_BOOKMAKERS_ACCOUNT1': os.getenv('ODDS_API_IO_BOOKMAKERS_ACCOUNT1', 'Bet365,Unibet'),
            'ODDS_API_IO_BOOKMAKERS_ACCOUNT2': os.getenv('ODDS_API_IO_BOOKMAKERS_ACCOUNT2', 'Betfair Exchange,Sbobet'),
            'ODDS_API_IO_PAGE_LIMIT': '100',
            'ODDS_API_IO_MAX_EVENT_PAGES_PER_SPORT': '36',
            'MAX_MATCHES_FOR_ODDS_FETCH': '520',
        },
    })

    sstats = patch_provider(providers, 'sstats', {
        'per_run_max': 150,
        'min_spacing_minutes': 0,
        'limit': {'requests_per_minute': 150, 'rules_source': 'RULES.txt'},
        'env': {
            'ENABLE_SSTATS': 'true',
            'ENABLE_SSTATS_CONTEXT': 'true',
            'SSTATS_ENABLED': 'true',
            'SSTATS_PER_RUN_MAX': '120',
            'SSTATS_REQUESTS_MAX_PER_RUN': '120',
            'SSTATS_MAX_HTTP_REQUESTS_PER_RUN': '120',
            'SSTATS_CONTEXT_MATCH_LIMIT': '260',
            'SSTATS_LOOKBACK_DAYS': '35',
            'SSTATS_RECENT_MATCHES': '10',
        },
    })

    thesportsdb = patch_provider(providers, 'thesportsdb', {
        'per_run_max': 30,
        'min_spacing_minutes': 0,
        'limit': {'requests_per_minute': 30, 'rules_source': 'RULES.txt'},
        'env': {
            'ENABLE_THESPORTSDB': 'true',
            'ENABLE_THESPORTSDB_CONTEXT': 'true',
            'THESPORTSDB_ENABLED': 'true',
            'THESPORTSDB_PER_RUN_MAX': '30',
            'THESPORTSDB_REQUESTS_MAX_PER_RUN': '30',
            'THESPORTSDB_CONTEXT_MATCH_LIMIT': '180',
        },
    })

    football = patch_provider(providers, 'football_data', {
        'per_run_max': 16,
        'min_spacing_minutes': 2,
        'limit': {'requests_per_minute_registered': 10, 'rules_source': 'RULES.txt'},
        'env': {
            'ENABLE_FOOTBALL_DATA': 'true',
            'ENABLE_FOOTBALL_DATA_CONTEXT': 'true',
            'FOOTBALL_DATA_ENABLED': 'true',
            'FOOTBALL_DATA_PER_RUN_MAX': '16',
            'FOOTBALL_DATA_REQUESTS_MAX_PER_RUN': '16',
            'FOOTBALL_DATA_CONTEXT_MATCH_LIMIT': '180',
        },
    })

    openfootball = patch_provider(providers, 'openfootball_public', {
        'per_run_max': 18,
        'min_spacing_minutes': 0,
        'env': {
            'ENABLE_OPENFOOTBALL_CONTEXT': 'true',
            'OPENFOOTBALL_ENABLED': 'true',
            'OPENFOOTBALL_CONTEXT_MATCH_LIMIT': '220',
            'OPENFOOTBALL_MAX_HTTP_REQUESTS_PER_RUN': '18',
            'OPENFOOTBALL_SKIP_404_CACHE_TTL_HOURS': '24',
        },
    })

    policy['version'] = 'v20-dual-account-odds-api-io-budget'
    write_json(POLICY_PATH, policy)

    env = {
        'RULES_API_BUDGET_POLICY_VERSION': policy['version'],
        'ALL_SOURCES_FREE_MAXIMIZE': 'false',
        'ODDS_API_IO_PER_RUN_MAX': odds_global,
        'ODDS_API_IO_MAX_HTTP_REQUESTS_PER_RUN': odds_global,
        'ODDS_API_IO_ACCOUNT1_PER_RUN_MAX': odds_account1,
        'ODDS_API_IO_ACCOUNT2_PER_RUN_MAX': odds_account2,
        'ODDS_API_IO_BOOKMAKERS_ACCOUNT1': os.getenv('ODDS_API_IO_BOOKMAKERS_ACCOUNT1', 'Bet365,Unibet'),
        'ODDS_API_IO_BOOKMAKERS_ACCOUNT2': os.getenv('ODDS_API_IO_BOOKMAKERS_ACCOUNT2', 'Betfair Exchange,Sbobet'),
        'ODDS_API_IO_MAX_EVENT_PAGES_PER_SPORT': '36',
        'MAX_MATCHES_FOR_ODDS_FETCH': '520',
        'SSTATS_PER_RUN_MAX': '120',
        'SSTATS_REQUESTS_MAX_PER_RUN': '120',
        'SSTATS_CONTEXT_MATCH_LIMIT': '260',
        'THESPORTSDB_PER_RUN_MAX': '30',
        'THESPORTSDB_REQUESTS_MAX_PER_RUN': '30',
        'THESPORTSDB_CONTEXT_MATCH_LIMIT': '180',
        'FOOTBALL_DATA_PER_RUN_MAX': '16',
        'FOOTBALL_DATA_REQUESTS_MAX_PER_RUN': '16',
        'FOOTBALL_DATA_CONTEXT_MATCH_LIMIT': '180',
        'OPENFOOTBALL_MAX_HTTP_REQUESTS_PER_RUN': '18',
        'OPENFOOTBALL_CONTEXT_MATCH_LIMIT': '220',
    }
    append_env(env)

    report = {
        'status': 'ok',
        'updated_at_utc': datetime.now(UTC).isoformat(),
        'policy_path': str(POLICY_PATH),
        'version': policy['version'],
        'patched': {
            'odds_api_io': {'per_run_max': odds.get('per_run_max'), 'limit': odds.get('limit'), 'env': odds.get('env')},
            'sstats': {'per_run_max': sstats.get('per_run_max'), 'env': sstats.get('env')},
            'thesportsdb': {'per_run_max': thesportsdb.get('per_run_max'), 'env': thesportsdb.get('env')},
            'football_data': {'per_run_max': football.get('per_run_max'), 'env': football.get('env')},
            'openfootball_public': {'per_run_max': openfootball.get('per_run_max'), 'env': openfootball.get('env')},
        },
        'reason': 'Align legacy odds budget boost with dual-account odds-api.io mode so it no longer downgrades provider max to 80.',
    }
    write_json(OUT, report)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
