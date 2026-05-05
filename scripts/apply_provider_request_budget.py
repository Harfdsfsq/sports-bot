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
EFFECTIVE_RUNTIME_PATH = ROOT / '.data' / 'exports' / 'latest-harizon-runtime-policy.json'
MARKET_INTEGRITY_CHECK_PATH = ROOT / '.data' / 'exports' / 'latest-market-integrity-runtime-check.json'
GITHUB_ENV = os.getenv('GITHUB_ENV')
UTC = timezone.utc
MSK = ZoneInfo(os.getenv('APP_TIMEZONE') or os.getenv('TZ') or 'Europe/Moscow')

REMOVED_PROVIDERS = {'api_football', 'bookies_api', 'oddspapi'}


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return default


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')


def append_github_env(values: dict[str, str]) -> None:
    for key, value in values.items():
        os.environ[str(key)] = str(value)
    if not GITHUB_ENV:
        for key in sorted(values):
            print(f'{key}={values[key]}')
        return
    with open(GITHUB_ENV, 'a', encoding='utf-8') as fh:
        for key in sorted(values):
            fh.write(f'{key}={values[key]}\n')


def truthy(value: object) -> bool:
    return str(value or '').strip().lower() in {'1', 'true', 'yes', 'on', 'force'}


def as_int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ''):
            return default
        return int(float(str(value)))
    except Exception:
        return default


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


def manual_probe_without_force_publish() -> bool:
    """Return True only for explicit manual probe/dry-run workflows.

    The previous policy made every workflow_dispatch a dry-run unless FORCE_PUBLISH
    was passed. That caused detailed reports to show a selected pick while the
    standalone Telegram prediction was not sent. Manual HARIZON run-bot launches
    should publish when the lifecycle/publisher guards approve a pick.
    """
    if str(os.getenv('GITHUB_EVENT_NAME') or '').strip() != 'workflow_dispatch':
        return False
    if truthy(os.getenv('MANUAL_CONTROLLED_PUBLISH_ENABLED') or 'true'):
        return False
    return not truthy(os.getenv('AUTORUN_INPUT_FORCE_PUBLISH') or os.getenv('FORCE_PUBLISH'))


def removed_provider_env() -> dict[str, str]:
    return {
        'ENABLE_BOOKIES_API': 'false',
        'BOOKIES_API_ENABLED': 'false',
        'BOOKIES_API_ODDS_FETCH_LIMIT': '0',
        'BOOKIES_API_REQUEST_BUDGET_GRANTED': '0',
        'BOOKIES_API_REQUEST_BUDGET_REASON': 'removed_from_project',
        'ENABLE_API_FOOTBALL': 'false',
        'API_FOOTBALL_ENABLED': 'false',
        'API_FOOTBALL_KEY': '',
        'API_FOOTBALL_PER_RUN_MAX': '0',
        'API_FOOTBALL_MAX_HTTP_REQUESTS_PER_RUN': '0',
        'API_FOOTBALL_CONTEXT_MATCH_LIMIT': '0',
        'API_FOOTBALL_REQUEST_BUDGET_GRANTED': '0',
        'API_FOOTBALL_REQUEST_BUDGET_REASON': 'removed_from_project',
        'ENABLE_ODDSPAPI': 'false',
        'ODDSPAPI_ENABLED': 'false',
        'ODDSPAPI_MATCH_LIMIT': '0',
        'ODDSPAPI_CONTEXT_MATCH_LIMIT': '0',
        'ODDSPAPI_PER_RUN_MAX': '0',
        'ODDSPAPI_MAX_HTTP_REQUESTS_PER_RUN': '0',
        'ODDSPAPI_REQUEST_BUDGET_GRANTED': '0',
        'ODDSPAPI_REQUEST_BUDGET_REASON': 'removed_from_project',
    }


def final_market_integrity_env() -> dict[str, str]:
    manual_dry_run = manual_probe_without_force_publish()
    fast_inventory = truthy(os.getenv('HARIZON_FAST_INVENTORY_LOCK') or os.getenv('DAY_INVENTORY_FAST_MODE') or 'true')
    inventory_merge = 'false' if fast_inventory else str(os.getenv('DAY_INVENTORY_FORCE_PROVIDER_MERGE') or 'false').lower()
    runtime_version = 'harizon-runtime-policy-v5-live-controlled-publish-fast-inventory'
    env = {
        'HARIZON_RUNTIME_POLICY_VERSION': runtime_version,
        'HARIZON_EFFECTIVE_RUNTIME_POLICY_VERSION': runtime_version,
        'HARIZON_FAST_INVENTORY_LOCK': 'true' if fast_inventory else 'false',
        'HARIZON_MANUAL_DRY_RUN': str(manual_dry_run).lower(),
        'MANUAL_CONTROLLED_PUBLISH_ENABLED': 'true',
        'PUBLISH_DRY_RUN': 'true' if manual_dry_run else 'false',
        'CONTROLLED_FALLBACK_DRY_RUN': 'true' if manual_dry_run else 'false',
        'CONTROLLED_FALLBACK_SEND_TELEGRAM': 'false' if manual_dry_run else 'true',
        'CONTROLLED_FALLBACK_TELEGRAM_ENABLED': 'false' if manual_dry_run else 'true',
        'MATCH_BOOTSTRAP_PROVIDER': os.getenv('MATCH_BOOTSTRAP_PROVIDER') or 'odds_api_io',
        'DAY_INVENTORY_BOOTSTRAP_PROVIDER': os.getenv('DAY_INVENTORY_BOOTSTRAP_PROVIDER') or 'odds_api_io',
        'DAY_INVENTORY_FORCE_PROVIDER_MERGE': inventory_merge,
        'DAY_INVENTORY_USE_FOR_RUN': 'true',
        'DAY_INVENTORY_COVERAGE_MAX_REBUILD': 'false' if fast_inventory else str(os.getenv('DAY_INVENTORY_COVERAGE_MAX_REBUILD') or 'false').lower(),
        'DAY_INVENTORY_NEAR_WINDOW_PRIORITY': 'true',
        'DAY_INVENTORY_NEAR_WINDOW_HOURS': os.getenv('DAY_INVENTORY_NEAR_WINDOW_HOURS') or '12',
        'MARKET_DERIVED_MIN_BOOKS': '2',
        'MARKET_DERIVED_MIN_SOURCES': '2',
        'MARKET_DERIVED_CONSENSUS_RELIEF_MIN_BOOKS': '2',
        'MARKET_DERIVED_CONSENSUS_RELIEF_MIN_SOURCES': '2',
        'CONTROLLED_FALLBACK_MIN_INDEPENDENT_SOURCES': '2',
        'CONTROLLED_FALLBACK_MIN_ODDS_SOURCES': '2',
        'CONTROLLED_FALLBACK_REQUIRE_2_BOOKS_FOR_TELEGRAM': 'true',
        'CONTROLLED_FALLBACK_REQUIRE_2_ODDS_SOURCES_FOR_TELEGRAM': 'true',
        'CONTROLLED_FALLBACK_REJECT_SINGLE_SOURCE_UNLESS_3_BOOKS': 'true',
        'CONTROLLED_FALLBACK_PROXY_SINGLE_SOURCE_STRICT': 'true',
        'CONTROLLED_FALLBACK_PROXY_SINGLE_SOURCE_MIN_CONFIDENCE': '78.0',
        'CONTROLLED_FALLBACK_PROXY_SINGLE_SOURCE_MIN_EDGE_PP': '8.0',
        'CONTROLLED_FALLBACK_PROXY_SINGLE_SOURCE_MIN_EV_PCT': '15.0',
        'CONTROLLED_FALLBACK_ALLOWED_FAMILIES': 'totals,dnb,btts',
        'CONTROLLED_FALLBACK_TIER_A_ALLOWED_FAMILIES': 'totals,dnb,btts',
        'CONTROLLED_FALLBACK_TIER_B_ALLOWED_FAMILIES': 'totals,dnb,btts',
        'CONTROLLED_FALLBACK_TIER_C_ALLOWED_FAMILIES': '',
        'CONTROLLED_FALLBACK_TIER_C_PUBLISH_ENABLED': 'false',
        'CONTROLLED_FALLBACK_TIER_A_MIN_BOOKS': '2',
        'CONTROLLED_FALLBACK_TIER_A_MIN_ODDS_SOURCES': '2',
        'CONTROLLED_FALLBACK_TIER_B_MIN_BOOKS': '2',
        'CONTROLLED_FALLBACK_TIER_B_MIN_ODDS_SOURCES': '2',
        'CONTROLLED_FALLBACK_FINAL_MIN_EV_PCT': '6.0',
        'CONTROLLED_FALLBACK_FINAL_MIN_EDGE_PP': '3.0',
        'DISABLE_SPREADS_UNTIL_HANDICAP_PARSER_VERIFIED': 'true',
        'HANDICAP_PAIR_INTEGRITY_REQUIRED': 'true',
        'SPREADS_PUBLICATION_ENABLED': 'false',
        'TEAM_TOTALS_PUBLICATION_ENABLED': 'false',
    }
    env.update(removed_provider_env())
    if manual_dry_run:
        env.update({
            'MIN_KICKOFF_LEAD_MINUTES': '0',
            'ADAPTIVE_MIN_KICKOFF_LEAD_ENABLED': 'false',
            'ADAPTIVE_MIN_KICKOFF_LEAD_MINUTES': '0',
            'EMERGENCY_MIN_KICKOFF_LEAD_ENABLED': 'true',
            'EMERGENCY_MIN_KICKOFF_LEAD_MINUTES': '0',
            'FORCE_RELAXED_MIN_KICKOFF_LEAD_ENABLED': 'true',
            'FORCE_RELAXED_MIN_KICKOFF_LEAD_MINUTES': '0',
            'MANUAL_LATE_MODE_ENABLED': 'true',
            'MANUAL_LATE_MIN_KICKOFF_LEAD_MINUTES': '0',
            'MANUAL_LATE_ADAPTIVE_MIN_KICKOFF_LEAD_MINUTES': '0',
        })
    return env


def market_integrity_check(env: dict[str, str], policy_version: str | None) -> dict[str, Any]:
    def families(name: str) -> set[str]:
        return {item.strip().lower() for item in str(env.get(name) or '').split(',') if item.strip()}

    failures: list[str] = []
    warnings: list[str] = []
    forbidden = {'spreads', 'teamtotals'}
    scopes = {
        'allowed': families('CONTROLLED_FALLBACK_ALLOWED_FAMILIES'),
        'tier_a': families('CONTROLLED_FALLBACK_TIER_A_ALLOWED_FAMILIES'),
        'tier_b': families('CONTROLLED_FALLBACK_TIER_B_ALLOWED_FAMILIES'),
        'tier_c': families('CONTROLLED_FALLBACK_TIER_C_ALLOWED_FAMILIES'),
    }
    for scope, values in scopes.items():
        leaked = sorted(values & forbidden)
        if leaked:
            failures.append(f'{scope}_contains_forbidden_families:{"/".join(leaked)}')
    if truthy(env.get('SPREADS_PUBLICATION_ENABLED')):
        failures.append('SPREADS_PUBLICATION_ENABLED=true')
    if truthy(env.get('TEAM_TOTALS_PUBLICATION_ENABLED')):
        failures.append('TEAM_TOTALS_PUBLICATION_ENABLED=true')
    if not truthy(env.get('DISABLE_SPREADS_UNTIL_HANDICAP_PARSER_VERIFIED')):
        failures.append('DISABLE_SPREADS_UNTIL_HANDICAP_PARSER_VERIFIED=false')
    min_odds_sources = as_int(env.get('CONTROLLED_FALLBACK_MIN_ODDS_SOURCES'))
    if min_odds_sources < 2:
        failures.append(f'CONTROLLED_FALLBACK_MIN_ODDS_SOURCES={min_odds_sources}')
    if not truthy(env.get('CONTROLLED_FALLBACK_REQUIRE_2_ODDS_SOURCES_FOR_TELEGRAM')):
        failures.append('CONTROLLED_FALLBACK_REQUIRE_2_ODDS_SOURCES_FOR_TELEGRAM=false')
    if manual_probe_without_force_publish():
        if not truthy(env.get('HARIZON_MANUAL_DRY_RUN')):
            failures.append('manual_workflow_without_force_publish_not_dry_run')
        if truthy(env.get('CONTROLLED_FALLBACK_SEND_TELEGRAM')):
            failures.append('manual_workflow_without_force_publish_can_send_controlled_telegram')
        if as_int(env.get('MIN_KICKOFF_LEAD_MINUTES'), 999) != 0:
            failures.append('manual_dry_run_min_kickoff_lead_not_zero')
    if str(env.get('SPORTLOGIC_BOOKMAKERS') or '') not in {'__probe_only__', ''}:
        warnings.append('sportlogic_bookmakers_not_probe_only')
    return {
        'status': 'failed' if failures else 'ok',
        'failures': failures,
        'warnings': warnings,
        'runtime_policy_version': env.get('HARIZON_EFFECTIVE_RUNTIME_POLICY_VERSION'),
        'provider_policy_version': policy_version,
        'checked': {
            'allowed_families': sorted(scopes['allowed']),
            'tier_a': sorted(scopes['tier_a']),
            'tier_b': sorted(scopes['tier_b']),
            'tier_c': sorted(scopes['tier_c']),
            'min_odds_sources': min_odds_sources,
            'manual_dry_run': env.get('HARIZON_MANUAL_DRY_RUN'),
            'controlled_send_telegram': env.get('CONTROLLED_FALLBACK_SEND_TELEGRAM'),
            'min_kickoff_lead_minutes': env.get('MIN_KICKOFF_LEAD_MINUTES'),
            'inventory_bootstrap': env.get('DAY_INVENTORY_BOOTSTRAP_PROVIDER'),
            'inventory_provider_merge': env.get('DAY_INVENTORY_FORCE_PROVIDER_MERGE'),
            'sportlogic_bookmakers': env.get('SPORTLOGIC_BOOKMAKERS'),
        },
    }


def load_policy() -> dict[str, Any]:
    policy = load_json(POLICY_PATH, {})
    if isinstance(policy, dict) and isinstance(policy.get('providers'), dict):
        return policy
    return {
        'version': 'v20-minimal-fallback',
        'mode': 'per_run_only',
        'deleted_providers': sorted(REMOVED_PROVIDERS),
        'base_env': {
            'PROVIDER_REQUEST_BUDGET_MODE': 'per_run_only',
            'PROVIDER_REQUEST_BUDGET_DISABLE_DAILY_MONTHLY': 'true',
            'ALL_SOURCES_FREE_MAXIMIZE': 'true',
            'CONTEXT_ENRICHMENT_REQUIRES_OFFERS': 'true',
        },
        'deleted_provider_env': removed_provider_env(),
        'providers': {},
    }


def compute(policy: dict[str, Any]) -> tuple[dict[str, str], list[dict[str, Any]], list[str]]:
    env: dict[str, str] = {str(k): str(v) for k, v in dict(policy.get('base_env') or {}).items()}
    env['PROVIDER_REQUEST_BUDGET_VERSION'] = str(policy.get('version') or 'unknown')
    env['PROVIDER_REQUEST_BUDGET_APPLIED'] = 'true'
    env.update(removed_provider_env())
    env.update({str(k): str(v) for k, v in dict(policy.get('deleted_provider_env') or {}).items() if str(k).lower() not in {'oddspapi_api_key'}})

    decisions: list[dict[str, Any]] = []
    for provider, raw_cfg in dict(policy.get('providers') or {}).items():
        provider_key = str(provider).strip().lower()
        if provider_key in REMOVED_PROVIDERS:
            decisions.append({'provider': provider, 'status': 'removed', 'grant': 0, 'configured_grant': 0, 'reason': 'removed_from_project', 'secret_env_keys': [], 'api_key_present': None})
            continue
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

    integrity = final_market_integrity_env()
    env.update(integrity)
    notes = [
        'config/provider_runtime_policy.json is the effective provider budget source of truth.',
        'bookies_api, api_football and oddspapi are removed from active runtime and provider budget decisions.',
        'Fast inventory lock prevents provider_request_budget from re-enabling heavy day-inventory provider merge.',
        'Controlled fallback can send standalone Telegram predictions on workflow_dispatch when lifecycle and publisher guards approve.',
        'SportLogic remains context/probe only unless its odds payload is explicitly verified.',
        'Controlled fallback is market-integrity hardened: totals/dnb/btts only, no spreads/teamtotals, min 2 odds sources.',
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
    integrity_env = final_market_integrity_env()
    check = market_integrity_check(env, policy.get('version'))
    export = {
        'version': policy.get('version'),
        'policy_path': str(POLICY_PATH),
        'event': os.getenv('GITHUB_EVENT_NAME') or '',
        'utc_now': now.isoformat(),
        'msk_now': now.astimezone(MSK).isoformat(),
        'slot_msk': now.astimezone(MSK).strftime('%H:%M MSK'),
        'mode': policy.get('mode') or 'per_run_only',
        'deleted_providers': sorted(REMOVED_PROVIDERS),
        'decisions': decisions,
        'env_written_count': len(env),
        'integrity_env': integrity_env,
        'market_integrity_check': check,
        'notes': notes,
    }
    effective_runtime = {
        'policy_version': integrity_env['HARIZON_EFFECTIVE_RUNTIME_POLICY_VERSION'],
        'provider_policy_version': policy.get('version'),
        'created_at_utc': now.isoformat(),
        'local_now': now.astimezone(MSK).isoformat(),
        'env_updates': env,
        'provider_decisions': decisions,
        'market_integrity_check': check,
        'notes': notes,
        'source': 'scripts/apply_provider_request_budget.py final market-integrity/lifecycle layer',
    }
    write_json(EXPORT_PATH, export)
    write_json(EFFECTIVE_RUNTIME_PATH, effective_runtime)
    write_json(MARKET_INTEGRITY_CHECK_PATH, check)
    print(json.dumps(export, ensure_ascii=False, indent=2, sort_keys=True))
    return 1 if check.get('status') == 'failed' else 0


if __name__ == '__main__':
    raise SystemExit(main())
