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

    # RULES.txt: odds-api.io allows 100 requests/hour.
    # Run cadence is every 2 hours, so 80/run stays under hourly pressure and
    # 1200/day prevents late-night runs from becoming line-less after manual runs.
    odds = patch_provider(providers, 'odds_api_io', {
        'per_run_max': 80,
        'safe_daily_budget': 1200,
        'min_spacing_minutes': 0,
        'limit': {'requests_per_hour': 100, 'bookmakers': 2, 'rules_source': 'RULES.txt'},
        'env': {
            'ENABLE_ODDS_API_IO': 'true',
            'ODDS_API_IO_ENABLED': 'true',
            'ODDS_API_IO_PER_RUN_MAX': '80',
            'ODDS_API_IO_PAGE_LIMIT': '100',
            'ODDS_API_IO_MAX_EVENT_PAGES_PER_SPORT': '36',
            'MAX_MATCHES_FOR_ODDS_FETCH': '320',
        },
    })

    football = patch_provider(providers, 'football_data', {
        'per_run_max': 12,
        'safe_daily_budget': 180,
        'min_spacing_minutes': 2,
        'env': {
            'FOOTBALL_DATA_PER_RUN_MAX': '12',
            'FOOTBALL_DATA_REQUESTS_MAX_PER_RUN': '12',
            'FOOTBALL_DATA_CONTEXT_MATCH_LIMIT': '140',
        },
    })

    policy['version'] = 'v18-rules-api-budget-odds-late-run-reserve'
    write_json(POLICY_PATH, policy)

    env = {
        'RULES_API_BUDGET_POLICY_VERSION': policy['version'],
        'ALL_SOURCES_FREE_MAXIMIZE': 'false',
        'ODDS_API_IO_PER_RUN_MAX': '80',
        'ODDS_API_IO_MAX_EVENT_PAGES_PER_SPORT': '36',
        'MAX_MATCHES_FOR_ODDS_FETCH': '320',
        'FOOTBALL_DATA_PER_RUN_MAX': '12',
        'FOOTBALL_DATA_REQUESTS_MAX_PER_RUN': '12',
        'FOOTBALL_DATA_CONTEXT_MATCH_LIMIT': '140',
    }
    append_env(env)

    report = {
        'status': 'ok',
        'updated_at_utc': datetime.now(UTC).isoformat(),
        'policy_path': str(POLICY_PATH),
        'version': policy['version'],
        'patched': {
            'odds_api_io': {
                'per_run_max': odds.get('per_run_max'),
                'safe_daily_budget': odds.get('safe_daily_budget'),
                'env': odds.get('env'),
            },
            'football_data': {
                'per_run_max': football.get('per_run_max'),
                'safe_daily_budget': football.get('safe_daily_budget'),
                'env': football.get('env'),
            },
        },
        'reason': 'odds_api_io was exhausted at 720/720 in late runs; RULES.txt allows 100 requests/hour, so 80/run and 1200/day preserve late-run odds coverage.',
    }
    write_json(OUT, report)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
