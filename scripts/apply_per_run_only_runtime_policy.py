from __future__ import annotations

"""Runtime policy: API budgets are per-run only, while picks target ~5/day.

This script is intentionally applied after the RULES/provider budget patch and
before scripts/apply_provider_request_budget.py. It removes planned daily/monthly
provider pre-budgets from config/provider_request_budget.json, so the request
budgeter grants only the configured per_run_max for each run. Fatal/auth cooldowns
are kept: they are not planned volume limits, they prevent retry storms after a
provider has already rejected requests.

For publications, this keeps the daily best-5 pacing logic but disables any hard
stop based on today's already-published count. After the target is reached the
existing governor still makes extra picks stricter, so the bot keeps selecting the
best opportunities instead of mechanically filling volume.
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path('.').resolve()
POLICY_PATH = ROOT / 'config' / 'provider_request_budget.json'
OUT = ROOT / '.data' / 'exports' / 'latest-per-run-only-runtime-policy.json'
GITHUB_ENV = os.getenv('GITHUB_ENV')
UTC = timezone.utc
POLICY_VERSION = 'v1-api-per-run-only-target5-no-daily-pick-cap'

REMOVED_PROVIDER_LIMIT_FIELDS = (
    'safe_daily_budget',
    'safe_monthly_budget',
    'min_spacing_minutes',
    'allowed_msk_hours',
    'manual_per_run_max',
)

BUDGET_ENV_MARKERS = (
    'DAILY_LIMIT',
    'DAILY_BUDGET',
    'MONTHLY_LIMIT',
    'MONTHLY_BUDGET',
)

UNLIMITED_ENV_VALUE = '999999'
NO_DAILY_PICK_CAP_SENTINEL = '999'


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
        limit['budget_scope'] = 'per_run_only'
        limit['daily_prebudget_disabled'] = True
        limit['monthly_prebudget_disabled'] = True
        provider['limit'] = limit

        changed[str(name)] = {
            'per_run_max': provider.get('per_run_max'),
            'removed_planned_limit_fields': removed_fields,
            'daily_or_monthly_env_values_lifted': env_budget_overrides,
        }

    policy['providers'] = providers
    policy['version'] = POLICY_VERSION
    policy['description'] = (
        'API request budgets are per-run only. Daily/monthly pre-budgets, spacing '
        'windows and allowed-hour gates are stripped at runtime; per_run_max remains '
        'the only planned request limit.'
    )
    notes = list(policy.get('notes') or []) if isinstance(policy.get('notes'), list) else []
    notes.append('Per-run-only runtime policy removed daily/monthly planned API budgets; only per_run_max is enforced before requests.')
    notes.append('Fatal/auth provider cooldowns are intentionally kept to avoid repeated calls after real provider rejection.')
    policy['notes'] = notes
    return changed


def publication_env() -> dict[str, str]:
    target = str(max(1, int(float(os.getenv('DAILY_TOP5_TARGET_PICKS') or os.getenv('DAILY_BEST5_TARGET_PICKS') or 5))))
    max_per_run = str(max(1, int(float(os.getenv('DAILY_TOP5_MAX_PICKS_PER_RUN') or os.getenv('MAX_PICKS_PER_RUN') or 2))))
    max_per_run = str(min(2, int(max_per_run)))

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
        # Keep the legacy maximize layer disabled, otherwise it can re-add
        # daily safe budgets after this policy has stripped them.
        'ALL_SOURCES_FREE_MAXIMIZE': 'false',
    }
    env.update(publication_env())
    append_env(env)

    report = {
        'status': 'ok',
        'version': POLICY_VERSION,
        'updated_at_utc': datetime.now(UTC).isoformat(),
        'provider_policy_path': str(POLICY_PATH),
        'providers_changed': changed,
        'applied_env': env,
        'summary': {
            'api_limits': 'planned API limits are per-run only via per_run_max',
            'publication_volume': 'daily hard cap disabled; governor still targets about 5 best picks/day and tightens after target',
            'max_picks_per_run': env['MAX_PICKS_PER_RUN'],
            'target_picks_per_day': env['DAILY_TOP5_TARGET_PICKS'],
            'daily_pick_hard_cap_sentinel': NO_DAILY_PICK_CAP_SENTINEL,
        },
    }
    write_json(OUT, report)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
