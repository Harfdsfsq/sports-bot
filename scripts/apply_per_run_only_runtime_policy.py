from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path('.').resolve()
POLICY_PATH = ROOT / 'config' / 'provider_request_budget.json'
OUT = ROOT / '.data' / 'exports' / 'latest-per-run-only-runtime-policy.json'
GITHUB_ENV = os.getenv('GITHUB_ENV')
UTC = timezone.utc
POLICY_VERSION = 'v2-api-per-run-only-with-capacity-layer'
NO_DAILY_PICK_CAP_SENTINEL = '999'

REMOVED_PROVIDER_LIMIT_FIELDS = ('safe_daily_budget', 'safe_monthly_budget', 'min_spacing_minutes', 'allowed_msk_hours', 'manual_per_run_max')
BUDGET_ENV_MARKERS = ('DAILY_LIMIT', 'DAILY_BUDGET', 'MONTHLY_LIMIT', 'MONTHLY_BUDGET')
UNLIMITED_ENV_VALUE = '999999'


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


def run_optional_script(path: str) -> dict[str, Any]:
    script = ROOT / path
    if not script.exists():
        return {'script': path, 'status': 'missing'}
    proc = subprocess.run([sys.executable, str(script)], cwd=str(ROOT), text=True, capture_output=True)
    return {'script': path, 'status': 'ok' if proc.returncode == 0 else 'failed', 'returncode': proc.returncode, 'stdout_tail': proc.stdout[-1200:], 'stderr_tail': proc.stderr[-1200:]}


def normalize_provider_env(provider: dict[str, Any]) -> dict[str, str]:
    env = provider.get('env')
    if not isinstance(env, dict):
        return {}
    changed: dict[str, str] = {}
    for key, value in list(env.items()):
        key_text = str(key)
        if any(marker in key_text for marker in BUDGET_ENV_MARKERS):
            if str(value) != UNLIMITED_ENV_VALUE:
                changed[key_text] = str(value)
            env[key_text] = UNLIMITED_ENV_VALUE
    provider['env'] = {str(k): str(v) for k, v in env.items()}
    return changed


def apply_provider_policy(policy: dict[str, Any]) -> dict[str, Any]:
    providers = policy.get('providers') if isinstance(policy.get('providers'), dict) else {}
    changed: dict[str, Any] = {}
    for name, provider in providers.items():
        if not isinstance(provider, dict):
            continue
        removed_fields: dict[str, Any] = {}
        for field in REMOVED_PROVIDER_LIMIT_FIELDS:
            if field in provider:
                removed_fields[field] = provider.pop(field)
        env_budget_overrides = normalize_provider_env(provider)
        limit = provider.get('limit') if isinstance(provider.get('limit'), dict) else {}
        limit.update({'budget_scope': 'per_run_only', 'daily_prebudget_disabled': True, 'monthly_prebudget_disabled': True})
        provider['limit'] = limit
        changed[str(name)] = {'per_run_max': provider.get('per_run_max'), 'removed_planned_limit_fields': removed_fields, 'daily_or_monthly_env_values_lifted': env_budget_overrides}
    policy['providers'] = providers
    policy['version'] = POLICY_VERSION
    policy['description'] = 'API request budgets are per-run only. Capacity overrides are applied by apply_api_capacity_and_keypool_policy.py.'
    notes = list(policy.get('notes') or []) if isinstance(policy.get('notes'), list) else []
    notes.append('Per-run-only runtime policy removed daily/monthly planned API budgets; only per_run_max is enforced before requests.')
    policy['notes'] = notes
    return changed


def publication_env() -> dict[str, str]:
    target = str(max(1, int(float(os.getenv('DAILY_TOP5_TARGET_PICKS') or os.getenv('DAILY_BEST5_TARGET_PICKS') or 5))))
    max_per_run = str(min(2, max(1, int(float(os.getenv('DAILY_TOP5_MAX_PICKS_PER_RUN') or os.getenv('MAX_PICKS_PER_RUN') or 2)))))
    return {
        'DAILY_TOP5_TARGET_PICKS': target,
        'DAILY_BEST5_TARGET_PICKS': target,
        'DAILY_TOP5_SOFT_TARGET_PICKS': target,
        'DAILY_TOP5_HARD_CAP_PICKS': NO_DAILY_PICK_CAP_SENTINEL,
        'DAILY_TOP5_NO_HARD_CAP': 'true',
        'VOLUME_DAILY_TARGET_PICKS': target,
        'VOLUME_DAILY_SOFT_CAP_PICKS': target,
        'VOLUME_DAILY_HARD_CAP_PICKS': NO_DAILY_PICK_CAP_SENTINEL,
        'VOLUME_NO_DAILY_HARD_CAP': 'true',
        'VOLUME_POLICY_MODE': 'daily_best5_no_hard_cap',
        'CONTROLLED_FALLBACK_DAILY_TARGET_MODE': 'target_5_average_no_daily_cap',
        'CONTROLLED_FALLBACK_MAX_PICKS_PER_RUN': max_per_run,
        'CONTROLLED_FALLBACK_ABSOLUTE_MAX_PICKS_PER_RUN': max_per_run,
        'MAX_PICKS_PER_RUN': max_per_run,
        'CONTROLLED_FALLBACK_MAX_PICKS_PER_MATCH': '1',
    }


def main() -> int:
    pre_scripts = [run_optional_script('scripts/apply_external_signals_runtime_patch.py')]
    policy = load_json(POLICY_PATH, {})
    if not isinstance(policy, dict):
        policy = {}
    changed = apply_provider_policy(policy)
    write_json(POLICY_PATH, policy)

    env = {
        'PER_RUN_ONLY_RUNTIME_POLICY_ACTIVE': 'true',
        'PER_RUN_ONLY_RUNTIME_POLICY_VERSION': POLICY_VERSION,
        'PROVIDER_REQUEST_BUDGET_SCOPE': 'per_run_only',
        'API_LIMIT_SCOPE': 'per_run_only',
        'API_DAILY_LIMITS_DISABLED': 'true',
        'API_MONTHLY_PREBUDGET_DISABLED': 'true',
        'ALL_SOURCES_FREE_MAXIMIZE': 'false',
    }
    env.update(publication_env())
    append_env(env)

    post_scripts = [run_optional_script('scripts/apply_api_capacity_and_keypool_policy.py')]
    report = {
        'status': 'ok',
        'version': POLICY_VERSION,
        'updated_at_utc': datetime.now(UTC).isoformat(),
        'provider_policy_path': str(POLICY_PATH),
        'providers_changed': changed,
        'applied_env': env,
        'pre_scripts': pre_scripts,
        'post_scripts': post_scripts,
        'summary': {'api_limits': 'per-run only; capacity layer sets odds_api_io=100/run and sstats=150/run', 'publication_volume': 'daily hard cap disabled; target remains about 5 best picks/day'},
    }
    write_json(OUT, report)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
