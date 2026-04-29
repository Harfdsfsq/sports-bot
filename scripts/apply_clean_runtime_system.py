from __future__ import annotations

"""Clean runtime system consolidation.

This script replaces the growing stack of one-off runtime patches with a small,
explicit set of runtime decisions:
  - Bzzoiro uses v2 provider implementation.
  - SStats uses a clean v1 provider implementation.
  - Bzzoiro has no planned request/context cap.
  - odds-api.io dual-account and SStats capacities remain explicit.
  - legacy budget files are normalized so old low caps do not win later.
"""

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path('.').resolve()
GITHUB_ENV = os.getenv('GITHUB_ENV')
POLICY_PATH = ROOT / 'config' / 'provider_request_budget.json'
OUT = ROOT / '.data' / 'exports' / 'latest-clean-runtime-system.json'
VERSION = 'v2-clean-bzzoiro-v2-sstats-v1'


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding='utf-8')) if path.exists() else default
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


def merge_provider(providers: dict[str, Any], name: str, patch: dict[str, Any]) -> dict[str, Any]:
    row = providers.get(name) if isinstance(providers.get(name), dict) else {}
    row = dict(row)
    for key, value in patch.items():
        if key in {'env', 'disable_env', 'limit'} and isinstance(value, dict):
            nested = dict(row.get(key) or {})
            nested.update(value)
            row[key] = nested
        else:
            row[key] = value
    providers[name] = row
    return row


def patch_runner_provider(module_from: str, module_to: str, attr: str, cls: str) -> bool:
    path = ROOT / 'app' / 'services' / 'runner.py'
    text = path.read_text(encoding='utf-8')
    old = f"self.{attr} = self._safe_provider('{module_from}', '{cls}')"
    new = f"self.{attr} = self._safe_provider('{module_to}', '{cls}')"
    if new in text:
        return False
    if old not in text:
        return False
    path.write_text(text.replace(old, new, 1), encoding='utf-8')
    return True


def apply_policy() -> dict[str, Any]:
    policy = load_json(POLICY_PATH, {})
    if not isinstance(policy, dict):
        policy = {}
    providers = policy.get('providers') if isinstance(policy.get('providers'), dict) else {}
    providers = dict(providers)

    bzz = merge_provider(providers, 'bzzoiro', {
        'enabled': True,
        'per_run_max': 0,
        'safe_daily_budget': 0,
        'safe_monthly_budget': 0,
        'min_spacing_minutes': 0,
        'limit': {
            'free_no_planned_limit': True,
            'api_version': 'v2',
            'budget_scope': 'unlimited_provider_no_prebudget',
        },
        'env': {
            'ENABLE_BZZOIRO': 'true',
            'ENABLE_BZZOIRO_CONTEXT': 'true',
            'BZZOIRO_ENABLED': 'true',
            'BZZOIRO_API_VERSION': 'v2',
            'BZZOIRO_BASE_URL': os.getenv('BZZOIRO_BASE_URL', 'https://sports.bzzoiro.com/api/v2'),
            'BZZOIRO_CONTEXT_MATCH_LIMIT': '0',
            'BZZOIRO_PER_RUN_MAX': '0',
            'BZZOIRO_MAX_HTTP_REQUESTS_PER_RUN': '0',
            'BZZOIRO_REQUEST_BUDGET_GRANTED': '999999',
            'BZZOIRO_REQUEST_BUDGET_REASON': 'unlimited_v2_no_planned_cap',
            'BZZOIRO_ENFORCE_CONTEXT_LIMIT': 'false',
            'BZZOIRO_V2_PAGE_SIZE': '200',
            'BZZOIRO_V2_MAX_EVENTS': '0',
            'BZZOIRO_V2_FETCH_EVENT_ODDS': 'true',
            'BZZOIRO_V2_FETCH_EVENT_STATS': 'true',
            'BZZOIRO_V2_FETCH_EVENT_METADATA': 'false',
        },
        'disable_env': {
            'ENABLE_BZZOIRO': 'false',
            'ENABLE_BZZOIRO_CONTEXT': 'false',
            'BZZOIRO_ENABLED': 'false',
            'BZZOIRO_REQUEST_BUDGET_GRANTED': '0',
        },
    })

    odds = merge_provider(providers, 'odds_api_io', {
        'enabled': True,
        'per_run_max': 140,
        'min_spacing_minutes': 0,
        'limit': {
            'requests_per_hour_per_account': 100,
            'bookmakers_per_account': 2,
            'budget_scope': 'per_run_dual_account',
            'account1_bookmakers': ['Bet365', 'Unibet'],
            'account2_bookmakers': ['Betfair Exchange', 'Sbobet'],
        },
        'env': {
            'ENABLE_ODDS_API_IO': 'true',
            'ODDS_API_IO_ENABLED': 'true',
            'ODDS_API_IO_PER_RUN_MAX': '140',
            'ODDS_API_IO_MAX_HTTP_REQUESTS_PER_RUN': '140',
            'ODDS_API_IO_ACCOUNT1_PER_RUN_MAX': '70',
            'ODDS_API_IO_ACCOUNT2_PER_RUN_MAX': '70',
            'ODDS_API_IO_BOOKMAKERS_ACCOUNT1': 'Bet365,Unibet',
            'ODDS_API_IO_BOOKMAKERS_ACCOUNT2': 'Betfair Exchange,Sbobet',
            'ODDS_API_IO_BOOKMAKERS': 'Bet365,Unibet',
            'ODDS_API_IO_PAGE_LIMIT': '100',
            'ODDS_API_IO_MAX_EVENT_PAGES_PER_SPORT': '36',
            'MAX_MATCHES_FOR_ODDS_FETCH': '320',
        },
    })

    sstats = merge_provider(providers, 'sstats', {
        'enabled': True,
        'per_run_max': 150,
        'safe_daily_budget': 0,
        'safe_monthly_budget': 0,
        'min_spacing_minutes': 0,
        'limit': {'requests_per_minute': 150, 'budget_scope': 'per_run'},
        'env': {
            'ENABLE_SSTATS': 'true',
            'ENABLE_SSTATS_CONTEXT': 'true',
            'SSTATS_ENABLED': 'true',
            'SSTATS_API_VERSION': 'v1',
            'SSTATS_BASE_URL': 'https://api.sstats.net',
            'SSTATS_PER_RUN_MAX': '150',
            'SSTATS_REQUESTS_MAX_PER_RUN': '150',
            'SSTATS_MAX_HTTP_REQUESTS_PER_RUN': '150',
            'SSTATS_CONTEXT_MATCH_LIMIT': '0',
            'SSTATS_LOOKBACK_DAYS': '35',
            'SSTATS_RECENT_MATCHES': '10',
            'SSTATS_DETAIL_MATCH_LIMIT': '80',
            'SSTATS_FETCH_LAST_GAMES_STATS': 'true',
            'SSTATS_FETCH_GLICKO': 'true',
            'SSTATS_FETCH_PROFITS': 'false',
        },
    })

    for name in ('football_data', 'thesportsdb'):
        if isinstance(providers.get(name), dict):
            providers[name].pop('safe_daily_budget', None)
            providers[name].pop('safe_monthly_budget', None)
            providers[name].setdefault('limit', {})['budget_scope'] = 'per_run_only'

    policy['providers'] = providers
    policy['version'] = VERSION
    policy['description'] = 'Clean runtime policy: Bzzoiro v2 unlimited, clean SStats v1 150/run, odds-api.io dual account, legacy low caps normalized.'
    notes = list(policy.get('notes') or []) if isinstance(policy.get('notes'), list) else []
    notes.append('Bzzoiro v2 is treated as unlimited/free: no per-run prebudget and no context match slicing unless BZZOIRO_ENFORCE_CONTEXT_LIMIT=true.')
    notes.append('SStats uses app.providers.sstats_v1 and SStats OpenAPI endpoints: /Games/list, /Games/last-games-stats, /Games/glicko/{id}.')
    notes.append('Legacy provider_request_budget.json low caps are overwritten by this clean policy before runtime.')
    policy['notes'] = notes[-12:]
    write_json(POLICY_PATH, policy)
    return {'bzzoiro': bzz, 'odds_api_io': odds, 'sstats': sstats}


def run_check() -> dict[str, Any]:
    result: dict[str, Any] = {'compiled': [], 'imports': []}
    files = ['app/providers/bzzoiro_v2.py', 'app/providers/sstats_v1.py', 'app/services/runner.py', 'scripts/publish_controlled_fallback.py']
    for rel in files:
        proc = subprocess.run([sys.executable, '-m', 'py_compile', rel], cwd=str(ROOT), text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        result['compiled'].append({'file': rel, 'returncode': proc.returncode, 'stderr_tail': proc.stderr[-500:]})
        if proc.returncode != 0:
            result['status'] = 'compile_failed'
            return result
    proc = subprocess.run([
        sys.executable,
        '-c',
        'from app.providers.bzzoiro_v2 import BzzoiroContextProvider; from app.providers.sstats_v1 import SStatsContextProvider; from app.services.runner import PredictionRunner; print("ok")'
    ], cwd=str(ROOT), text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    result['imports'].append({'returncode': proc.returncode, 'stdout': proc.stdout.strip(), 'stderr_tail': proc.stderr[-500:]})
    result['status'] = 'ok' if proc.returncode == 0 else 'import_failed'
    return result


def main() -> int:
    runner_bzz_changed = patch_runner_provider('app.providers.bzzoiro', 'app.providers.bzzoiro_v2', 'bzzoiro', 'BzzoiroContextProvider')
    runner_sstats_changed = patch_runner_provider('app.providers.sstats', 'app.providers.sstats_v1', 'sstats', 'SStatsContextProvider')
    providers = apply_policy()
    env = {
        'CLEAN_RUNTIME_SYSTEM_ACTIVE': 'true',
        'CLEAN_RUNTIME_SYSTEM_VERSION': VERSION,
        'BZZOIRO_API_VERSION': 'v2',
        'BZZOIRO_BASE_URL': os.getenv('BZZOIRO_BASE_URL', 'https://sports.bzzoiro.com/api/v2'),
        'BZZOIRO_CONTEXT_MATCH_LIMIT': '0',
        'BZZOIRO_PER_RUN_MAX': '0',
        'BZZOIRO_MAX_HTTP_REQUESTS_PER_RUN': '0',
        'BZZOIRO_REQUEST_BUDGET_GRANTED': '999999',
        'BZZOIRO_REQUEST_BUDGET_REASON': 'unlimited_v2_no_planned_cap',
        'BZZOIRO_ENFORCE_CONTEXT_LIMIT': 'false',
        'BZZOIRO_V2_PAGE_SIZE': '200',
        'BZZOIRO_V2_MAX_EVENTS': '0',
        'BZZOIRO_V2_FETCH_EVENT_ODDS': 'true',
        'BZZOIRO_V2_FETCH_EVENT_STATS': 'true',
        'SSTATS_API_VERSION': 'v1',
        'SSTATS_BASE_URL': 'https://api.sstats.net',
        'SSTATS_PER_RUN_MAX': '150',
        'SSTATS_REQUESTS_MAX_PER_RUN': '150',
        'SSTATS_MAX_HTTP_REQUESTS_PER_RUN': '150',
        'SSTATS_CONTEXT_MATCH_LIMIT': '0',
        'SSTATS_DETAIL_MATCH_LIMIT': '80',
        'SSTATS_FETCH_LAST_GAMES_STATS': 'true',
        'SSTATS_FETCH_GLICKO': 'true',
        'SSTATS_FETCH_PROFITS': 'false',
        'ODDS_API_IO_PER_RUN_MAX': '140',
        'ODDS_API_IO_MAX_HTTP_REQUESTS_PER_RUN': '140',
    }
    append_env(env)
    check = run_check()
    report = {
        'status': check.get('status'),
        'version': VERSION,
        'runner_bzzoiro_v2_changed': runner_bzz_changed,
        'runner_sstats_v1_changed': runner_sstats_changed,
        'env': env,
        'providers': providers,
        'check': check,
    }
    write_json(OUT, report)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if check.get('status') == 'ok' else 1


if __name__ == '__main__':
    raise SystemExit(main())
