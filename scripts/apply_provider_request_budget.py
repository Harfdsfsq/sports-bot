from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path('.').resolve()
POLICY_PATH = ROOT / 'config' / 'provider_request_budget.json'
STATE_PATH = ROOT / '.data' / 'provider_request_budget_state.json'
EXPORT_PATH = ROOT / '.data' / 'exports' / 'latest-provider-request-budget.json'
GITHUB_ENV = os.getenv('GITHUB_ENV')
UTC = timezone.utc
MSK = ZoneInfo(os.getenv('APP_TIMEZONE') or os.getenv('TZ') or 'Europe/Moscow')


def load_json(path: Path, default: Any) -> Any:
    try:
        if not path.exists():
            return default
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return default


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding='utf-8')


def parse_dt(value: Any) -> datetime | None:
    if value in (None, ''):
        return None
    try:
        text = str(value).strip()
        if text.endswith('Z'):
            text = text[:-1] + '+00:00'
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)
    except Exception:
        return None


def now_utc() -> datetime:
    return datetime.now(UTC)


def today_key(dt: datetime) -> str:
    return dt.astimezone(MSK).date().isoformat()


def month_key(dt: datetime) -> str:
    local = dt.astimezone(MSK)
    return f'{local.year:04d}-{local.month:02d}'


def as_int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ''):
            return default
        return int(float(value))
    except Exception:
        return default


def env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == '':
        return default
    return str(raw).strip().lower() in {'1', 'true', 'yes', 'on'}


def append_github_env(env: dict[str, str]) -> None:
    if not GITHUB_ENV:
        return
    with open(GITHUB_ENV, 'a', encoding='utf-8') as fh:
        for key in sorted(env):
            value = str(env[key])
            if '\n' in value:
                marker = f'EOF_{key}'
                fh.write(f'{key}<<{marker}\n{value}\n{marker}\n')
            else:
                fh.write(f'{key}={value}\n')


def provider_state(state: dict[str, Any], name: str) -> dict[str, Any]:
    providers = state.setdefault('providers', {})
    row = providers.setdefault(name, {})
    if not isinstance(row, dict):
        row = {}
        providers[name] = row
    row.setdefault('daily', {})
    row.setdefault('monthly', {})
    return row


def usage(row: dict[str, Any], period: str, key: str) -> int:
    bucket = row.get(period)
    if not isinstance(bucket, dict):
        return 0
    return as_int(bucket.get(key), 0)


def add_usage(row: dict[str, Any], period: str, key: str, amount: int) -> None:
    bucket = row.setdefault(period, {})
    if not isinstance(bucket, dict):
        bucket = {}
        row[period] = bucket
    bucket[key] = usage(row, period, key) + max(0, int(amount))


def is_watchdog_slot(now: datetime) -> bool:
    local = now.astimezone(MSK)
    return local.minute == 47 and local.hour in {1, 7, 13, 19}


def current_slot_label(now: datetime) -> str:
    local = now.astimezone(MSK)
    return f'{local.hour:02d}:{local.minute:02d} MSK'


def collect_recent_text() -> str:
    # Best-effort scan of recent artifacts/exports for fatal provider patterns. This is intentionally cheap.
    chunks: list[str] = []
    for path in [
        ROOT / '.data' / 'exports' / 'latest-provider-quota-governor.json',
        ROOT / '.data' / 'exports' / 'latest-provider-request-usage.json',
        ROOT / '.data' / 'exports' / 'latest-rapidapi-provider-probe.json',
        ROOT / 'artifacts' / 'controlled-fallback-report.json',
        ROOT / '.logs' / 'debug-last-run.json',
    ]:
        try:
            if path.exists() and path.is_file():
                text = path.read_text(encoding='utf-8', errors='ignore')
                chunks.append(text[:200000])
        except Exception:
            pass
    return '\n'.join(chunks).lower()


def fatal_cooldown_until(now: datetime, cfg: dict[str, Any], recent_text: str) -> tuple[str | None, str | None]:
    for pattern in cfg.get('fatal_patterns') or []:
        try:
            if str(pattern).lower() in recent_text:
                days = as_int(cfg.get('fatal_cooldown_days'), 0) or 0
                hours = as_int(cfg.get('fatal_cooldown_hours'), 0) or 0
                if days <= 0 and hours <= 0:
                    hours = 6
                until = now + timedelta(days=days, hours=hours)
                return until.isoformat(), f'fatal_pattern:{pattern}'
        except Exception:
            continue
    for pattern in cfg.get('auth_patterns') or []:
        try:
            if str(pattern).lower() in recent_text:
                hours = as_int(cfg.get('auth_cooldown_hours'), 24)
                until = now + timedelta(hours=max(1, hours))
                return until.isoformat(), f'auth_pattern:{pattern}'
        except Exception:
            continue
    return None, None


def decide_provider(name: str, cfg: dict[str, Any], row: dict[str, Any], now: datetime, recent_text: str) -> dict[str, Any]:
    event = os.getenv('GITHUB_EVENT_NAME') or ''
    is_manual = event == 'workflow_dispatch'
    is_push = event == 'push'
    local_hour = now.astimezone(MSK).hour
    dkey = today_key(now)
    mkey = month_key(now)

    decision = {
        'provider': name,
        'enabled_by_policy': bool(cfg.get('enabled', True)),
        'grant': 0,
        'reason': 'not_evaluated',
        'slot': current_slot_label(now),
        'daily_used_before': usage(row, 'daily', dkey),
        'monthly_used_before': usage(row, 'monthly', mkey),
    }

    # Detect recent fatal conditions from prior artifacts. If detected, persist cooldown immediately.
    until, why = fatal_cooldown_until(now, cfg, recent_text)
    if until:
        row['cooldown_until'] = until
        row['cooldown_reason'] = why

    cooldown_until = parse_dt(row.get('cooldown_until'))
    if cooldown_until and cooldown_until > now:
        decision.update({'reason': f'cooldown_active:{row.get("cooldown_reason") or "provider"}', 'cooldown_until': cooldown_until.isoformat()})
        return decision

    if not bool(cfg.get('enabled', True)):
        decision['reason'] = 'disabled_by_policy'
        return decision

    allowed_hours = cfg.get('allowed_msk_hours')
    if isinstance(allowed_hours, list) and not is_manual:
        hours = {as_int(x, -1) for x in allowed_hours}
        if local_hour not in hours:
            decision['reason'] = f'slot_not_allowed:{local_hour:02d}MSK'
            return decision

    min_spacing = as_int(cfg.get('min_spacing_minutes'), 0)
    last_grant = parse_dt(row.get('last_grant_at'))
    if last_grant and min_spacing > 0 and not is_manual:
        elapsed = (now - last_grant).total_seconds() / 60.0
        if elapsed < min_spacing:
            decision['reason'] = f'spacing_active:{elapsed:.1f}m/{min_spacing}m'
            return decision

    per_run = as_int(cfg.get('per_run_max'), 0)
    if is_push:
        # Push runs should be cheap; odds can still run but monthly providers should not.
        per_run = min(per_run, as_int(cfg.get('push_per_run_max'), 0))
    if is_manual:
        per_run = max(per_run, as_int(cfg.get('manual_per_run_max'), per_run))

    daily_budget = as_int(cfg.get('safe_daily_budget'), 0)
    monthly_budget = as_int(cfg.get('safe_monthly_budget'), 0)
    daily_remaining = daily_budget - usage(row, 'daily', dkey) if daily_budget > 0 else per_run
    monthly_remaining = monthly_budget - usage(row, 'monthly', mkey) if monthly_budget > 0 else per_run
    grant = max(0, min(per_run, daily_remaining, monthly_remaining))

    if grant <= 0:
        if daily_budget > 0 and daily_remaining <= 0:
            decision['reason'] = f'daily_budget_exhausted:{usage(row, "daily", dkey)}/{daily_budget}'
        elif monthly_budget > 0 and monthly_remaining <= 0:
            decision['reason'] = f'monthly_budget_exhausted:{usage(row, "monthly", mkey)}/{monthly_budget}'
        else:
            decision['reason'] = 'per_run_zero'
        return decision

    decision.update({
        'grant': grant,
        'reason': 'granted',
        'daily_remaining_after': max(0, daily_remaining - grant) if daily_budget > 0 else None,
        'monthly_remaining_after': max(0, monthly_remaining - grant) if monthly_budget > 0 else None,
    })
    row['last_grant_at'] = now.isoformat()
    row['last_grant'] = grant
    row['last_decision_reason'] = 'granted'
    add_usage(row, 'daily', dkey, grant)
    add_usage(row, 'monthly', mkey, grant)
    return decision


def build_env_for_decision(cfg: dict[str, Any], decision: dict[str, Any]) -> dict[str, str]:
    grant = as_int(decision.get('grant'), 0)
    if grant <= 0:
        env = dict(cfg.get('disable_env') or {})
    else:
        env = dict(cfg.get('env') or {})
    # Generic signal for debugging/optional provider support.
    prefix = str(decision['provider']).upper().replace('-', '_')
    env[f'{prefix}_REQUEST_BUDGET_GRANTED'] = str(grant)
    env[f'{prefix}_REQUEST_BUDGET_REASON'] = str(decision.get('reason') or '')
    env[f'{prefix}_MAX_HTTP_REQUESTS_PER_RUN'] = str(grant)
    return {str(k): str(v) for k, v in env.items()}


def main() -> int:
    policy = load_json(POLICY_PATH, {})
    if not isinstance(policy, dict):
        policy = {}
    state = load_json(STATE_PATH, {})
    if not isinstance(state, dict):
        state = {}
    now = now_utc()
    recent_text = collect_recent_text()
    providers = policy.get('providers') if isinstance(policy.get('providers'), dict) else {}

    all_env: dict[str, str] = {
        'PROVIDER_REQUEST_BUDGET_VERSION': str(policy.get('version') or 'v12-api-request-budget'),
        'PROVIDER_REQUEST_BUDGET_APPLIED': 'true',
        'PROVIDER_REQUEST_BUDGET_SLOT_MSK': current_slot_label(now),
    }
    decisions: list[dict[str, Any]] = []
    for name, cfg in providers.items():
        if not isinstance(cfg, dict):
            continue
        row = provider_state(state, str(name))
        decision = decide_provider(str(name), cfg, row, now, recent_text)
        decisions.append(decision)
        all_env.update(build_env_for_decision(cfg, decision))

    append_github_env(all_env)
    state['version'] = str(policy.get('version') or 'v12-api-request-budget')
    state['updated_at'] = now.isoformat()
    write_json(STATE_PATH, state)
    export = {
        'version': str(policy.get('version') or 'v12-api-request-budget'),
        'event': os.getenv('GITHUB_EVENT_NAME') or '',
        'utc_now': now.isoformat(),
        'msk_now': now.astimezone(MSK).isoformat(),
        'slot_msk': current_slot_label(now),
        'is_watchdog_slot': is_watchdog_slot(now),
        'decisions': decisions,
        'env_written_count': len(all_env),
        'notes': [
            'This is a pre-run budget gate. It caps provider env values before app.cli run-once.',
            'For strict accounting, provider implementations should also decrement one token per real HTTP request.',
            'Monthly providers are intentionally sparse; bzzoiro and odds-api.io carry broad coverage.'
        ],
    }
    write_json(EXPORT_PATH, export)
    print(json.dumps(export, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
