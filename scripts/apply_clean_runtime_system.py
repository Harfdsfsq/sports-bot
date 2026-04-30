from __future__ import annotations

"""Clean runtime system consolidation.

This script replaces the growing stack of one-off runtime patches with a small,
explicit set of runtime decisions:
  - Bzzoiro uses documented /api/predictions/ context provider.
  - SStats uses a clean v1 provider implementation.
  - RapidAPI odds bridge is plugged into the existing AllSportsAPI offer slot.
  - Bzzoiro has no planned request/context cap.
  - odds-api.io dual-account and SStats capacities remain explicit.
  - legacy budget files are normalized so old low caps do not win later.
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path('.').resolve()
GITHUB_ENV = os.getenv('GITHUB_ENV')
POLICY_PATH = ROOT / 'config' / 'provider_request_budget.json'
OUT = ROOT / '.data' / 'exports' / 'latest-clean-runtime-system.json'
VERSION = 'v4-bzzoiro-predictions-rapidapi-bridge'


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
    new = f"self.{attr} = self._safe_provider('{module_to}', '{cls}')"
    if new in text:
        return False
    pattern = rf"self\.{re.escape(attr)}\s*=\s*self\._safe_provider\([^\n]+\)"
    updated, count = re.subn(pattern, new, text, count=1)
    if count <= 0:
        old = f"self.{attr} = self._safe_provider('{module_from}', '{cls}')"
        if old not in text:
            return False
        updated = text.replace(old, new, 1)
    path.write_text(updated, encoding='utf-8')
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
        'limit': {'free_no_planned_limit': True, 'api_version': 'v2_predictions', 'budget_scope': 'unlimited_provider_no_prebudget'},
        'env': {
            'ENABLE_BZZOIRO': 'true', 'ENABLE_BZZOIRO_CONTEXT': 'true', 'BZZOIRO_ENABLED': 'true',
            'BZZOIRO_API_VERSION': 'v2_predictions', 'BZZOIRO_BASE_URL': os.getenv('BZZOIRO_BASE_URL', 'https://sports.bzzoiro.com/api/v2'),
            'BZZOIRO_API_ROOT_URL': os.getenv('BZZOIRO_API_ROOT_URL', 'https://sports.bzzoiro.com/api'),
            'BZZOIRO_CONTEXT_MATCH_LIMIT': '520', 'BZZOIRO_PER_RUN_MAX': '1000', 'BZZOIRO_MAX_HTTP_REQUESTS_PER_RUN': '1000',
            'BZZOIRO_REQUEST_BUDGET_GRANTED': '999999', 'BZZOIRO_REQUEST_BUDGET_REASON': 'unlimited_v2_predictions_no_planned_cap',
            'BZZOIRO_ENFORCE_CONTEXT_LIMIT': 'false', 'BZZOIRO_PREDICTIONS_PAGE_SIZE': '100', 'BZZOIRO_PREDICTIONS_MAX_PAGES': '6',
        },
        'disable_env': {'ENABLE_BZZOIRO': 'false', 'ENABLE_BZZOIRO_CONTEXT': 'false', 'BZZOIRO_ENABLED': 'false', 'BZZOIRO_REQUEST_BUDGET_GRANTED': '0'},
    })

    odds = merge_provider(providers, 'odds_api_io', {
        'enabled': True,
        'per_run_max': 200,
        'min_spacing_minutes': 0,
        'limit': {'requests_per_hour_per_account': 100, 'bookmakers_per_account': 2, 'budget_scope': 'per_run_dual_account', 'account1_bookmakers': ['Bet365', 'Unibet'], 'account2_bookmakers': ['Betfair Exchange', 'Sbobet']},
        'env': {
            'ENABLE_ODDS_API_IO': 'true', 'ODDS_API_IO_ENABLED': 'true', 'ODDS_API_IO_PER_RUN_MAX': '200', 'ODDS_API_IO_MAX_HTTP_REQUESTS_PER_RUN': '200',
            'ODDS_API_IO_ACCOUNT1_PER_RUN_MAX': '100', 'ODDS_API_IO_ACCOUNT2_PER_RUN_MAX': '100',
            'ODDS_API_IO_BOOKMAKERS_ACCOUNT1': 'Bet365,Unibet', 'ODDS_API_IO_BOOKMAKERS_ACCOUNT2': 'Betfair Exchange,Sbobet', 'ODDS_API_IO_BOOKMAKERS': 'Bet365,Unibet',
            'ODDS_API_IO_PAGE_LIMIT': '100', 'ODDS_API_IO_MAX_EVENT_PAGES_PER_SPORT': '36', 'MAX_MATCHES_FOR_ODDS_FETCH': '520',
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
            'ENABLE_SSTATS': 'true', 'ENABLE_SSTATS_CONTEXT': 'true', 'SSTATS_ENABLED': 'true', 'SSTATS_API_VERSION': 'v1', 'SSTATS_BASE_URL': 'https://api.sstats.net',
            'SSTATS_PER_RUN_MAX': '150', 'SSTATS_REQUESTS_MAX_PER_RUN': '150', 'SSTATS_MAX_HTTP_REQUESTS_PER_RUN': '150', 'SSTATS_CONTEXT_MATCH_LIMIT': '320',
            'SSTATS_LOOKBACK_DAYS': '35', 'SSTATS_RECENT_MATCHES': '10', 'SSTATS_DETAIL_MATCH_LIMIT': '80',
            'SSTATS_FETCH_LAST_GAMES_STATS': 'true', 'SSTATS_FETCH_GLICKO': 'true', 'SSTATS_FETCH_PROFITS': 'false',
        },
    })

    rapidapi_odds = merge_provider(providers, 'rapidapi_odds_bridge', {
        'enabled': True,
        'per_run_max': 24,
        'min_spacing_minutes': 0,
        'limit': {'budget_scope': 'per_run_probe_then_parse', 'hosts': ['sportapi7.p.rapidapi.com', 'sportsbook-api2.p.rapidapi.com', 'odds-api1.p.rapidapi.com', 'odds-feed.p.rapidapi.com', 'free-api-live-football-data.p.rapidapi.com']},
        'env': {
            'ENABLE_RAPIDAPI_ODDS_BRIDGE': 'true',
            'ENABLE_ALLSPORTSAPI': 'true',
            'ALLSPORTSAPI_ENABLED': 'true',
            'RAPIDAPI_ODDS_MAX_HTTP_REQUESTS_PER_RUN': '24',
            'RAPIDAPI_ODDS_MATCH_LIMIT': '36',
            'RAPIDAPI_ODDS_CACHE_TTL_MINUTES': '45',
            'SPORTAPI7_RAPIDAPI_HOST': 'sportapi7.p.rapidapi.com',
            'SPORTSBOOK_RAPIDAPI_HOST': 'sportsbook-api2.p.rapidapi.com',
            'ODDS_API1_RAPIDAPI_HOST': 'odds-api1.p.rapidapi.com',
            'ODDS_FEED_RAPIDAPI_HOST': 'odds-feed.p.rapidapi.com',
            'FREE_FOOTBALL_RAPIDAPI_HOST': 'free-api-live-football-data.p.rapidapi.com',
            'SPORTSBOOK_API_RAPIDAPI_PER_RUN_MAX': '8',
            'ODDS_FEED_RAPIDAPI_PER_RUN_MAX': '6',
            'ODDS_API1_RAPIDAPI_PER_RUN_MAX': '4',
            'SPORTAPI7_RAPIDAPI_ODDS_PER_RUN_MAX': '4',
            'FREE_FOOTBALL_RAPIDAPI_ODDS_PER_RUN_MAX': '4',
        },
    })

    for name in ('football_data', 'thesportsdb'):
        if isinstance(providers.get(name), dict):
            providers[name].pop('safe_daily_budget', None)
            providers[name].pop('safe_monthly_budget', None)
            providers[name].setdefault('limit', {})['budget_scope'] = 'per_run_only'

    policy['providers'] = providers
    policy['version'] = VERSION
    policy['description'] = 'Clean runtime policy: Bzzoiro predictions endpoint, clean SStats v1 150/run, odds-api.io dual account, RapidAPI odds bridge enabled.'
    notes = list(policy.get('notes') or []) if isinstance(policy.get('notes'), list) else []
    notes.append('Bzzoiro now uses /api/predictions/ because v2 live stats can be null before kickoff.')
    notes.append('RapidAPI odds bridge is wired through runner.allsportsapi slot to avoid runner surgery while adding sportsbook/oddsfeed/sportapi/free-football hosts.')
    notes.append('SStats uses app.providers.sstats_v1 and SStats OpenAPI endpoints: /Games/list, /Games/last-games-stats, /Games/glicko/{id}.')
    policy['notes'] = notes[-12:]
    write_json(POLICY_PATH, policy)
    return {'bzzoiro': bzz, 'odds_api_io': odds, 'sstats': sstats, 'rapidapi_odds_bridge': rapidapi_odds}


def run_check() -> dict[str, Any]:
    result: dict[str, Any] = {'compiled': [], 'imports': []}
    files = ['app/providers/bzzoiro_predictions_v2.py', 'app/providers/sstats_v1.py', 'app/providers/rapidapi_odds_bridge.py', 'app/services/runner.py', 'scripts/publish_controlled_fallback.py']
    for rel in files:
        proc = subprocess.run([sys.executable, '-m', 'py_compile', rel], cwd=str(ROOT), text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        result['compiled'].append({'file': rel, 'returncode': proc.returncode, 'stderr_tail': proc.stderr[-500:]})
        if proc.returncode != 0:
            result['status'] = 'compile_failed'
            return result
    proc = subprocess.run([
        sys.executable, '-c',
        'from app.providers.bzzoiro_predictions_v2 import BzzoiroContextProvider; from app.providers.sstats_v1 import SStatsContextProvider; from app.providers.rapidapi_odds_bridge import RapidApiOddsBridgeProvider; from app.services.runner import PredictionRunner; print("ok")'
    ], cwd=str(ROOT), text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    result['imports'].append({'returncode': proc.returncode, 'stdout': proc.stdout.strip(), 'stderr_tail': proc.stderr[-500:]})
    result['status'] = 'ok' if proc.returncode == 0 else 'import_failed'
    return result


def main() -> int:
    runner_bzz_changed = patch_runner_provider('app.providers.bzzoiro_v2', 'app.providers.bzzoiro_predictions_v2', 'bzzoiro', 'BzzoiroContextProvider')
    runner_sstats_changed = patch_runner_provider('app.providers.sstats', 'app.providers.sstats_v1', 'sstats', 'SStatsContextProvider')
    runner_rapidapi_changed = patch_runner_provider('app.providers.allsportsapi', 'app.providers.rapidapi_odds_bridge', 'allsportsapi', 'RapidApiOddsBridgeProvider')
    providers = apply_policy()
    env = {
        'CLEAN_RUNTIME_SYSTEM_ACTIVE': 'true',
        'CLEAN_RUNTIME_SYSTEM_VERSION': VERSION,
        'BZZOIRO_API_VERSION': 'v2_predictions',
        'BZZOIRO_BASE_URL': os.getenv('BZZOIRO_BASE_URL', 'https://sports.bzzoiro.com/api/v2'),
        'BZZOIRO_API_ROOT_URL': os.getenv('BZZOIRO_API_ROOT_URL', 'https://sports.bzzoiro.com/api'),
        'BZZOIRO_CONTEXT_MATCH_LIMIT': '520', 'BZZOIRO_PER_RUN_MAX': '1000', 'BZZOIRO_MAX_HTTP_REQUESTS_PER_RUN': '1000',
        'BZZOIRO_REQUEST_BUDGET_GRANTED': '999999', 'BZZOIRO_REQUEST_BUDGET_REASON': 'unlimited_v2_predictions_no_planned_cap', 'BZZOIRO_ENFORCE_CONTEXT_LIMIT': 'false',
        'BZZOIRO_PREDICTIONS_PAGE_SIZE': '100', 'BZZOIRO_PREDICTIONS_MAX_PAGES': '6',
        'SSTATS_API_VERSION': 'v1', 'SSTATS_BASE_URL': 'https://api.sstats.net', 'SSTATS_PER_RUN_MAX': '150', 'SSTATS_REQUESTS_MAX_PER_RUN': '150',
        'SSTATS_MAX_HTTP_REQUESTS_PER_RUN': '150', 'SSTATS_CONTEXT_MATCH_LIMIT': '320', 'SSTATS_DETAIL_MATCH_LIMIT': '80', 'SSTATS_FETCH_LAST_GAMES_STATS': 'true',
        'SSTATS_FETCH_GLICKO': 'true', 'SSTATS_FETCH_PROFITS': 'false',
        'ODDS_API_IO_PER_RUN_MAX': '200', 'ODDS_API_IO_MAX_HTTP_REQUESTS_PER_RUN': '200',
        'ENABLE_RAPIDAPI_ODDS_BRIDGE': 'true', 'ENABLE_ALLSPORTSAPI': 'true', 'ALLSPORTSAPI_ENABLED': 'true',
        'RAPIDAPI_ODDS_MAX_HTTP_REQUESTS_PER_RUN': '24', 'RAPIDAPI_ODDS_MATCH_LIMIT': '36', 'RAPIDAPI_ODDS_CACHE_TTL_MINUTES': '45',
        'SPORTAPI7_RAPIDAPI_HOST': 'sportapi7.p.rapidapi.com', 'SPORTSBOOK_RAPIDAPI_HOST': 'sportsbook-api2.p.rapidapi.com',
        'ODDS_API1_RAPIDAPI_HOST': 'odds-api1.p.rapidapi.com', 'ODDS_FEED_RAPIDAPI_HOST': 'odds-feed.p.rapidapi.com', 'FREE_FOOTBALL_RAPIDAPI_HOST': 'free-api-live-football-data.p.rapidapi.com',
    }
    append_env(env)
    check = run_check()
    report = {
        'status': check.get('status'),
        'version': VERSION,
        'runner_bzzoiro_predictions_changed': runner_bzz_changed,
        'runner_sstats_v1_changed': runner_sstats_changed,
        'runner_rapidapi_odds_bridge_changed': runner_rapidapi_changed,
        'env': env,
        'providers': providers,
        'check': check,
    }
    write_json(OUT, report)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if check.get('status') == 'ok' else 1


if __name__ == '__main__':
    raise SystemExit(main())
