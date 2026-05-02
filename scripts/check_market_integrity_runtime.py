from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

OUT = Path('.data/exports/latest-market-integrity-runtime-check.json')
RUNTIME_PATH = Path('.data/exports/latest-harizon-runtime-policy.json')
BUDGET_PATH = Path('.data/exports/latest-provider-request-budget.json')


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return default


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')


def truthy(value: Any) -> bool:
    return str(value or '').strip().lower() in {'1', 'true', 'yes', 'on', 'force'}


def env_value(env: dict[str, Any], key: str) -> str:
    value = env.get(key)
    if value is None:
        value = os.getenv(key)
    return str(value or '').strip()


def as_int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ''):
            return default
        return int(float(str(value)))
    except Exception:
        return default


def main() -> int:
    runtime = load_json(RUNTIME_PATH, {})
    budget = load_json(BUDGET_PATH, {})
    env = runtime.get('env_updates') if isinstance(runtime.get('env_updates'), dict) else {}
    integrity = budget.get('integrity_env') if isinstance(budget.get('integrity_env'), dict) else {}
    combined: dict[str, Any] = {}
    combined.update(env)
    combined.update(integrity)

    failures: list[str] = []
    warnings: list[str] = []

    allowed_families = {item.strip().lower() for item in env_value(combined, 'CONTROLLED_FALLBACK_ALLOWED_FAMILIES').split(',') if item.strip()}
    tier_a = {item.strip().lower() for item in env_value(combined, 'CONTROLLED_FALLBACK_TIER_A_ALLOWED_FAMILIES').split(',') if item.strip()}
    tier_b = {item.strip().lower() for item in env_value(combined, 'CONTROLLED_FALLBACK_TIER_B_ALLOWED_FAMILIES').split(',') if item.strip()}
    tier_c = {item.strip().lower() for item in env_value(combined, 'CONTROLLED_FALLBACK_TIER_C_ALLOWED_FAMILIES').split(',') if item.strip()}
    forbidden = {'spreads', 'teamtotals'}
    for scope, families in {'allowed': allowed_families, 'tier_a': tier_a, 'tier_b': tier_b, 'tier_c': tier_c}.items():
        leaked = sorted(families & forbidden)
        if leaked:
            failures.append(f'{scope}_contains_forbidden_families:{"/".join(leaked)}')

    if truthy(env_value(combined, 'SPREADS_PUBLICATION_ENABLED')):
        failures.append('SPREADS_PUBLICATION_ENABLED=true')
    if truthy(env_value(combined, 'TEAM_TOTALS_PUBLICATION_ENABLED')):
        failures.append('TEAM_TOTALS_PUBLICATION_ENABLED=true')
    if not truthy(env_value(combined, 'DISABLE_SPREADS_UNTIL_HANDICAP_PARSER_VERIFIED')):
        failures.append('DISABLE_SPREADS_UNTIL_HANDICAP_PARSER_VERIFIED=false')

    min_odds_sources = as_int(env_value(combined, 'CONTROLLED_FALLBACK_MIN_ODDS_SOURCES'))
    if min_odds_sources < 2:
        failures.append(f'CONTROLLED_FALLBACK_MIN_ODDS_SOURCES={min_odds_sources}')
    if not truthy(env_value(combined, 'CONTROLLED_FALLBACK_REQUIRE_2_ODDS_SOURCES_FOR_TELEGRAM')):
        failures.append('CONTROLLED_FALLBACK_REQUIRE_2_ODDS_SOURCES_FOR_TELEGRAM=false')

    event_name = env_value(combined, 'GITHUB_EVENT_NAME') or os.getenv('GITHUB_EVENT_NAME') or str(budget.get('event') or '')
    force_publish = truthy(os.getenv('AUTORUN_INPUT_FORCE_PUBLISH') or os.getenv('FORCE_PUBLISH'))
    if event_name == 'workflow_dispatch' and not force_publish:
        if not truthy(env_value(combined, 'HARIZON_MANUAL_DRY_RUN')):
            failures.append('manual_workflow_without_force_publish_not_dry_run')
        if truthy(env_value(combined, 'CONTROLLED_FALLBACK_SEND_TELEGRAM')):
            failures.append('manual_workflow_without_force_publish_can_send_controlled_telegram')

    sportlogic_reason = ''
    for row in budget.get('decisions') if isinstance(budget.get('decisions'), list) else []:
        if isinstance(row, dict) and str(row.get('provider')) == 'sportlogic':
            sportlogic_reason = str(row.get('reason') or '')
            break
    if env_value(combined, 'SPORTLOGIC_BOOKMAKERS') != '__probe_only__':
        warnings.append('sportlogic_bookmakers_not_probe_only')
    if 'quarantined' not in sportlogic_reason and 'quarantined' not in env_value(combined, 'SPORTLOGIC_ODDS_DISABLED_REASON'):
        warnings.append('sportlogic_odds_quarantine_reason_missing')

    payload = {
        'status': 'failed' if failures else 'ok',
        'failures': failures,
        'warnings': warnings,
        'runtime_policy_version': runtime.get('policy_version') or env_value(combined, 'HARIZON_EFFECTIVE_RUNTIME_POLICY_VERSION'),
        'provider_policy_version': budget.get('version') or runtime.get('provider_policy_version'),
        'checked': {
            'allowed_families': sorted(allowed_families),
            'tier_a': sorted(tier_a),
            'tier_b': sorted(tier_b),
            'tier_c': sorted(tier_c),
            'min_odds_sources': min_odds_sources,
            'manual_dry_run': env_value(combined, 'HARIZON_MANUAL_DRY_RUN'),
            'controlled_send_telegram': env_value(combined, 'CONTROLLED_FALLBACK_SEND_TELEGRAM'),
            'sportlogic_bookmakers': env_value(combined, 'SPORTLOGIC_BOOKMAKERS'),
        },
    }
    write_json(OUT, payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 1 if failures else 0


if __name__ == '__main__':
    raise SystemExit(main())
