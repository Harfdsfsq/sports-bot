from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path('.').resolve()
POLICY_PATH = ROOT / 'config' / 'provider_runtime_policy.json'
STATE_PATH = ROOT / '.data' / 'provider_request_budget_state.json'
EXPORT_PATH = ROOT / '.data' / 'exports' / 'latest-provider-request-budget.json'
GITHUB_ENV = os.getenv('GITHUB_ENV')
UTC = timezone.utc
MSK = ZoneInfo(os.getenv('APP_TIMEZONE') or os.getenv('TZ') or 'Europe/Moscow')


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return default


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')


def append_github_env(values: dict[str, str]) -> None:
    if not GITHUB_ENV:
        for key in sorted(values):
            print(f'{key}={values[key]}')
        return
    with open(GITHUB_ENV, 'a', encoding='utf-8') as fh:
        for key in sorted(values):
            fh.write(f'{key}={values[key]}\n')


def env_present(keys: list[str]) -> bool:
    return any(str(os.getenv(key) or '').strip() for key in keys)


def prefix(provider: str) -> str:
    return provider.upper().replace('-', '_')


def disabled_env(provider: str) -> dict[str, str]:
    p = prefix(provider)
    return {
        f'{p}_PER_RUN_MAX': '0',
        f'{p}_MAX_HTTP_REQUESTS_PER_RUN': '0',
        f'{p}_REQUEST_BUDGET_GRANTED': '0',
    }


def load_policy() -> dict[str, Any]:
    policy = load_json(POLICY_PATH, {})
    if isinstance(policy, dict) and isinstance(policy.get('providers'), dict):
        return policy
    return {
        'version': 'v20-minimal-fallback',
        'mode': 'per_run_only',
        'deleted_providers': ['api_football'],
        'base_env': {
            'PROVIDER_REQUEST_BUDGET_MODE': 'per_run_only',
            'PROVIDER_REQUEST_BUDGET_DISABLE_DAILY_MONTHLY': 'true',
            'ALL_SOURCES_FREE_MAXIMIZE': 'true',
            'CONTEXT_ENRICHMENT_REQUIRES_OFFERS': 'true',
        },
        'deleted_provider_env': {
            'ENABLE_API_FOOTBALL': 'false',
            'API_FOOTBALL_ENABLED': 'false',
            'API_FOOTBALL_KEY': '',
            'API_FOOTBALL_PER_RUN_MAX': '0',
            'API_FOOTBALL_MAX_HTTP_REQUESTS_PER_RUN': '0',
            'API_FOOTBALL_CONTEXT_MATCH_LIMIT': '0',
            'API_FOOTBALL_REQUEST_BUDGET_REASON': 'removed_from_project',
        },
        'providers': {},
    }


def compute(policy: dict[str, Any]) -> tuple[dict[str, str], list[dict[str, Any]], list[str]]:
    env: dict[str, str] = {str(k): str(v) for k, v in dict(policy.get('base_env') or {}).items()}
    env['PROVIDER_REQUEST_BUDGET_VERSION'] = str(policy.get('version') or 'unknown')
    env['PROVIDER_REQUEST_BUDGET_APPLIED'] = 'true'
    env.update({str(k): str(v) for k, v in dict(policy.get('deleted_provider_env') or {}).items()})

    decisions: list[dict[str, Any]] = []
    for provider, raw_cfg in dict(policy.get('providers') or {}).items():
        cfg = raw_cfg if isinstance(raw_cfg, dict) else {}
        configured_grant = max(0, int(float(cfg.get('grant') or 0)))
        secret_keys = [str(item) for item in (cfg.get('secret_env_keys') or []) if str(item).strip()]
        missing_key = bool(secret_keys) and not env_present(secret_keys)
        grant = 0 if missing_key and configured_grant > 0 else configured_grant
        reason = str(cfg.get('reason') or ('missing_key' if missing_key else 'granted' if grant > 0 else 'disabled_by_policy'))
        status = str(cfg.get('status') or ('missing_key' if missing_key else 'working' if grant > 0 else 'disabled_by_policy'))

        provider_env = {str(k): str(v) for k, v in dict(cfg.get('env') or {}).items()}
        if missing_key and configured_grant > 0:
            provider_env.update(disabled_env(str(provider)))
        env.update(provider_env)
        p = prefix(str(provider))
        env[f'{p}_REQUEST_BUDGET_GRANTED'] = str(grant)
        env[f'{p}_REQUEST_BUDGET_REASON'] = reason
        env.setdefault(f'{p}_MAX_HTTP_REQUESTS_PER_RUN', str(grant))
        decisions.append({
            'provider': provider,
            'status': status,
            'grant': grant,
            'configured_grant': configured_grant,
            'reason': reason,
            'secret_env_keys': secret_keys,
            'api_key_present': None if not secret_keys else not missing_key,
        })

    notes = [
        'config/provider_runtime_policy.json is the effective provider budget source of truth.',
        'api-football is removed from active runtime and report provider lists.',
        'SportLogic remains disabled until raw odds payload price parsing is fixture-tested.',
        'Budget mode is per-run-only; daily/monthly counters are not used for critical free sources.',
    ]
    if env_present(['ODDS_API_IO_KEY_2']):
        env['ODDS_API_IO_ACCOUNT2_ACTIVE'] = 'true'
    else:
        env['ODDS_API_IO_ACCOUNT2_ACTIVE'] = 'false'
        notes.append('ODDS_API_IO_KEY_2 is missing; Betfair Exchange/Sbobet account2 cannot be queried.')
    return env, decisions, notes


def main() -> int:
    now = datetime.now(UTC)
    policy = load_policy()
    env, decisions, notes = compute(policy)
    append_github_env(env)
    state = load_json(STATE_PATH, {})
    if not isinstance(state, dict):
        state = {}
    state.update({'version': policy.get('version'), 'policy_path': str(POLICY_PATH), 'updated_at': now.isoformat(), 'last_decisions': decisions})
    write_json(STATE_PATH, state)
    export = {
        'version': policy.get('version'),
        'policy_path': str(POLICY_PATH),
        'event': os.getenv('GITHUB_EVENT_NAME') or '',
        'utc_now': now.isoformat(),
        'msk_now': now.astimezone(MSK).isoformat(),
        'slot_msk': now.astimezone(MSK).strftime('%H:%M MSK'),
        'mode': policy.get('mode') or 'per_run_only',
        'deleted_providers': policy.get('deleted_providers') or ['api_football'],
        'decisions': decisions,
        'env_written_count': len(env),
        'notes': notes,
    }
    write_json(EXPORT_PATH, export)
    print(json.dumps(export, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
