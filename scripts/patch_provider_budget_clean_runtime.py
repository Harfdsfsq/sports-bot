from __future__ import annotations

from pathlib import Path

TARGET = Path('scripts/apply_provider_request_budget.py')
PATCH_VERSION = 'v1-clean-runtime-provider-budget'


def main() -> int:
    text = TARGET.read_text(encoding='utf-8')
    changed = False

    if 'CLEAN_RUNTIME_PROVIDER_BUDGET_PATCH_VERSION' not in text:
        marker = 'UTC = timezone.utc\n'
        if marker in text:
            text = text.replace(marker, marker + f'CLEAN_RUNTIME_PROVIDER_BUDGET_PATCH_VERSION = "{PATCH_VERSION}"\n', 1)
            changed = True

    # Provider-specific grant override before generic daily/monthly budget logic.
    old = '''def decide_provider(name: str, cfg: dict[str, Any], row: dict[str, Any], now: datetime, recent_text: str) -> dict[str, Any]:
    event = os.getenv('GITHUB_EVENT_NAME') or ''
'''
    new = '''def decide_provider(name: str, cfg: dict[str, Any], row: dict[str, Any], now: datetime, recent_text: str) -> dict[str, Any]:
    provider_name = str(name or '').strip().lower()
    if provider_name == 'bzzoiro':
        row['last_grant_at'] = now.isoformat()
        row['last_grant'] = 999999
        row['last_decision_reason'] = 'unlimited_v2_no_planned_cap'
        return {
            'provider': name,
            'enabled_by_policy': True,
            'grant': 999999,
            'reason': 'unlimited_v2_no_planned_cap',
            'slot': current_slot_label(now),
            'daily_used_before': usage(row, 'daily', today_key(now)),
            'monthly_used_before': usage(row, 'monthly', month_key(now)),
            'daily_budget': 0,
            'monthly_budget': 0,
            'api_version': 'v2',
        }
    event = os.getenv('GITHUB_EVENT_NAME') or ''
'''
    if old in text:
        text = text.replace(old, new, 1)
        changed = True

    old = '''def build_env_for_decision(cfg: dict[str, Any], decision: dict[str, Any]) -> dict[str, str]:
    grant = as_int(decision.get('grant'), 0)
    if grant <= 0:
        env = dict(cfg.get('disable_env') or {})
        for key in cfg.get('secret_env_keys') or []:
            env[str(key)] = ''
    else:
        env = dict(cfg.get('env') or {})
    prefix = str(decision['provider']).upper().replace('-', '_')
    env[f'{prefix}_REQUEST_BUDGET_GRANTED'] = str(grant)
    env[f'{prefix}_REQUEST_BUDGET_REASON'] = str(decision.get('reason') or '')
    env.setdefault(f'{prefix}_MAX_HTTP_REQUESTS_PER_RUN', str(grant))
    return {str(k): str(v) for k, v in env.items()}
'''
    new = '''def build_env_for_decision(cfg: dict[str, Any], decision: dict[str, Any]) -> dict[str, str]:
    provider_name = str(decision.get('provider') or '').strip().lower()
    if provider_name == 'bzzoiro':
        env = dict(cfg.get('env') or {})
        env.update({
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
        })
        return {str(k): str(v) for k, v in env.items()}
    grant = as_int(decision.get('grant'), 0)
    if grant <= 0:
        env = dict(cfg.get('disable_env') or {})
        for key in cfg.get('secret_env_keys') or []:
            env[str(key)] = ''
    else:
        env = dict(cfg.get('env') or {})
    prefix = str(decision['provider']).upper().replace('-', '_')
    env[f'{prefix}_REQUEST_BUDGET_GRANTED'] = str(grant)
    env[f'{prefix}_REQUEST_BUDGET_REASON'] = str(decision.get('reason') or '')
    env.setdefault(f'{prefix}_MAX_HTTP_REQUESTS_PER_RUN', str(grant))
    return {str(k): str(v) for k, v in env.items()}
'''
    if old in text:
        text = text.replace(old, new, 1)
        changed = True

    if changed:
        TARGET.write_text(text, encoding='utf-8')
    print({'patch': PATCH_VERSION, 'changed': changed})
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
