from __future__ import annotations

import json
import os
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
ALL_HOURS = list(range(24))
HARIZON_CRITICAL_PROVIDERS = {
    'odds_api_io',
    'bzzoiro',
    'sstats',
    'football_data',
    'thesportsdb',
    'openfootball_public',
    'sportlogic',
}
HARIZON_RECOVERY_GRANTS = {
    'odds_api_io': 200,
    'bzzoiro': 1000,
    'sstats': 150,
    'football_data': 8,
    'thesportsdb': 12,
    'openfootball_public': 8,
    'sportlogic': 40,
}

FREE_PROVIDER_OVERRIDES: dict[str, dict[str, Any]] = {
    'odds_api_io': {
        'per_run_max': 200,
        'safe_daily_budget': 4800,
        'env': {
            'ODDS_API_IO_ACCOUNT1_PER_RUN_MAX': '100',
            'ODDS_API_IO_ACCOUNT2_PER_RUN_MAX': '100',
            'ODDS_API_IO_PER_RUN_MAX': '200',
            'ODDS_API_IO_MAX_HTTP_REQUESTS_PER_RUN': '200',
            'ODDS_API_IO_PAGE_LIMIT': '100',
            'ODDS_API_IO_MAX_EVENT_PAGES_PER_SPORT': '36',
            'MAX_MATCHES_FOR_ODDS_FETCH': '520',
        },
        'disable_env': {
            'ODDS_API_IO_PER_RUN_MAX': '0',
            'ODDS_API_IO_MAX_HTTP_REQUESTS_PER_RUN': '0',
            'MAX_MATCHES_FOR_ODDS_FETCH': '0',
        },
    },
    'bzzoiro': {
        'per_run_max': 1000,
        'safe_daily_budget': 200000,
        'env': {
            'BZZOIRO_PER_RUN_MAX': '1000',
            'BZZOIRO_MAX_HTTP_REQUESTS_PER_RUN': '1000',
            'BZZOIRO_CONTEXT_MATCH_LIMIT': '520',
            'BZZOIRO_MAX_PAGES': '80',
        },
        'disable_env': {
            'BZZOIRO_PER_RUN_MAX': '0',
            'BZZOIRO_MAX_HTTP_REQUESTS_PER_RUN': '0',
            'BZZOIRO_CONTEXT_MATCH_LIMIT': '0',
        },
    },
    'sstats': {
        'per_run_max': 150,
        'safe_daily_budget': 50000,
        'env': {
            'SSTATS_TARGET_NEAR_MISS_FIRST': 'true',
            'SSTATS_PER_RUN_MAX': '150',
            'SSTATS_REQUESTS_MAX_PER_RUN': '150',
            'SSTATS_MAX_HTTP_REQUESTS_PER_RUN': '150',
            'SSTATS_CONTEXT_MATCH_LIMIT': '320',
            'SSTATS_LOOKBACK_DAYS': '45',
            'SSTATS_RECENT_MATCHES': '10',
        },
        'disable_env': {
            'SSTATS_PER_RUN_MAX': '0',
            'SSTATS_REQUESTS_MAX_PER_RUN': '0',
            'SSTATS_MAX_HTTP_REQUESTS_PER_RUN': '0',
            'SSTATS_CONTEXT_MATCH_LIMIT': '0',
        },
    },
    'sportlogic': {
        'per_run_max': 40,
        'safe_daily_budget': 960,
        'min_spacing_minutes': 0,
        'env': {
            'ENABLE_SPORTLOGIC': 'true',
            'SPORTLOGIC_ENABLED': 'true',
            'SPORTLOGIC_BASE_URL': 'https://api.sportlogic.io/api/v1',
            'SPORTLOGIC_HEADER_NAME': 'X-API-Key',
            'SPORTLOGIC_PER_RUN_MAX': '40',
            'SPORTLOGIC_MATCH_LIMIT': '120',
            'SPORTLOGIC_ODDS_MATCH_LIMIT': '40',
            'SPORTLOGIC_TIMEOUT_SECONDS': '20',
        },
        'disable_env': {
            'ENABLE_SPORTLOGIC': 'false',
            'SPORTLOGIC_ENABLED': 'false',
            'SPORTLOGIC_PER_RUN_MAX': '0',
            'SPORTLOGIC_MATCH_LIMIT': '0',
            'SPORTLOGIC_ODDS_MATCH_LIMIT': '0',
        },
    },
    'espn_public': {
        'per_run_max': 18,
        'safe_daily_budget': 216,
        'allowed_msk_hours': ALL_HOURS,
        'env': {
            'ESPN_CONTEXT_MATCH_LIMIT': '16',
            'ESPN_MAX_MATCHES': '20',
            'ESPN_MAX_HTTP_REQUESTS_PER_RUN': '18',
            'ESPN_SCOREBOARD_MAX_REQUESTS_PER_RUN': '10',
            'ESPN_PROBABILITY_MAX_REQUESTS_PER_RUN': '4',
            'ESPN_SUMMARY_MAX_REQUESTS_PER_RUN': '4',
            'ESPN_SLUGS_PER_RUN_LIMIT': '3',
        },
        'disable_env': {
            'ESPN_CONTEXT_MATCH_LIMIT': '0',
            'ESPN_MAX_MATCHES': '0',
            'ESPN_MAX_HTTP_REQUESTS_PER_RUN': '0',
        },
    },
    'football_data': {
        'per_run_max': 8,
        'safe_daily_budget': 96,
        'min_spacing_minutes': 0,
        'env': {
            'FOOTBALL_DATA_PER_RUN_MAX': '8',
            'FOOTBALL_DATA_REQUESTS_MAX_PER_RUN': '8',
            'FOOTBALL_DATA_CONTEXT_MATCH_LIMIT': '48',
        },
        'disable_env': {
            'FOOTBALL_DATA_PER_RUN_MAX': '0',
            'FOOTBALL_DATA_REQUESTS_MAX_PER_RUN': '0',
            'FOOTBALL_DATA_CONTEXT_MATCH_LIMIT': '0',
        },
    },
    'thesportsdb': {
        'per_run_max': 12,
        'safe_daily_budget': 180,
        'min_spacing_minutes': 0,
        'env': {
            'THESPORTSDB_PER_RUN_MAX': '12',
            'THESPORTSDB_REQUESTS_MAX_PER_RUN': '12',
            'THESPORTSDB_CONTEXT_MATCH_LIMIT': '72',
        },
        'disable_env': {
            'THESPORTSDB_PER_RUN_MAX': '0',
            'THESPORTSDB_REQUESTS_MAX_PER_RUN': '0',
            'THESPORTSDB_CONTEXT_MATCH_LIMIT': '0',
        },
    },
    'openfootball_public': {
        'per_run_max': 8,
        'safe_daily_budget': 192,
        'allowed_msk_hours': ALL_HOURS,
        'env': {
            'OPENFOOTBALL_CONTEXT_MATCH_LIMIT': '120',
            'OPENFOOTBALL_MAX_HTTP_REQUESTS_PER_RUN': '8',
        },
        'disable_env': {
            'OPENFOOTBALL_CONTEXT_MATCH_LIMIT': '0',
            'OPENFOOTBALL_MAX_HTTP_REQUESTS_PER_RUN': '0',
        },
    },
    'newsapi_currents': {
        'per_run_max': 4,
        'safe_daily_budget': 72,
        'min_spacing_minutes': 60,
        'allowed_msk_hours': ALL_HOURS,
        'env': {
            'NEWSAPI_PER_RUN_MAX': '2',
            'NEWSAPI_MAX_HTTP_REQUESTS_PER_RUN': '2',
            'NEWSAPI_MATCH_LIMIT': '2',
            'NEWS_CONTEXT_MATCH_LIMIT': '6',
            'CURRENTS_NEWS_PER_RUN_MAX': '4',
            'CURRENTS_NEWS_MAX_HTTP_REQUESTS_PER_RUN': '4',
            'CURRENTS_MATCH_LIMIT': '4',
            'NEWS_CONTEXT_CACHE_TTL_MINUTES': '120',
        },
        'disable_env': {
            'NEWSAPI_PER_RUN_MAX': '0',
            'NEWSAPI_MAX_HTTP_REQUESTS_PER_RUN': '0',
            'NEWSAPI_MATCH_LIMIT': '0',
            'NEWS_CONTEXT_MATCH_LIMIT': '0',
            'CURRENTS_NEWS_PER_RUN_MAX': '0',
            'CURRENTS_NEWS_MAX_HTTP_REQUESTS_PER_RUN': '0',
            'CURRENTS_MATCH_LIMIT': '0',
        },
    },
    'gnews': {
        'per_run_max': 2,
        'safe_daily_budget': 36,
        'min_spacing_minutes': 60,
        'allowed_msk_hours': ALL_HOURS,
        'env': {
            'GNEWS_PER_RUN_MAX': '2',
            'GNEWS_MAX_HTTP_REQUESTS_PER_RUN': '2',
            'GNEWS_CONTEXT_MATCH_LIMIT': '4',
            'GNEWS_MATCH_LIMIT': '2',
        },
        'disable_env': {
            'GNEWS_PER_RUN_MAX': '0',
            'GNEWS_MAX_HTTP_REQUESTS_PER_RUN': '0',
            'GNEWS_CONTEXT_MATCH_LIMIT': '0',
            'GNEWS_MATCH_LIMIT': '0',
        },
    },
    'allsportsapi': {
        'per_run_max': 2,
        'safe_daily_budget': 24,
        'min_spacing_minutes': 120,
        'allowed_msk_hours': ALL_HOURS,
        'env': {
            'ALLSPORTSAPI_PER_RUN_MAX': '2',
            'ALLSPORTSAPI_MATCH_LIMIT': '16',
            'ALLSPORTSAPI_CONTEXT_MATCH_LIMIT': '8',
        },
        'disable_env': {
            'ALLSPORTSAPI_PER_RUN_MAX': '0',
            'ALLSPORTSAPI_MATCH_LIMIT': '0',
            'ALLSPORTSAPI_CONTEXT_MATCH_LIMIT': '0',
        },
    },
    'oddspapi': {
        'per_run_max': 1,
        'safe_daily_budget': 8,
        'safe_monthly_budget': 250,
        'allowed_msk_hours': ALL_HOURS,
    },
    'futrixmetrics': {
        'per_run_max': 4,
        'safe_daily_budget': 60,
        'safe_monthly_budget': 1500,
        'min_spacing_minutes': 60,
        'allowed_msk_hours': ALL_HOURS,
        'env': {
            'FUTRIXMETRICS_PER_RUN_MAX': '4',
            'FUTRIXMETRICS_REQUESTS_MAX_PER_RUN': '4',
            'FUTRIXMETRICS_CONTEXT_MATCH_LIMIT': '8',
            'FUTRIXMETRICS_SHORTLIST_ONLY': 'false',
            'FUTRIXMETRICS_MIN_SPACING_MINUTES': '60',
        },
        'disable_env': {
            'FUTRIXMETRICS_PER_RUN_MAX': '0',
            'FUTRIXMETRICS_REQUESTS_MAX_PER_RUN': '0',
            'FUTRIXMETRICS_CONTEXT_MATCH_LIMIT': '0',
        },
    },
    'weatherapi': {
        'per_run_max': 12,
        'safe_daily_budget': 720,
        'safe_monthly_budget': 20000,
        'env': {
            'WEATHERAPI_PER_RUN_MAX': '12',
            'WEATHERAPI_MAX_HTTP_REQUESTS_PER_RUN': '12',
            'WEATHER_CONTEXT_MATCH_LIMIT': '20',
            'WEATHER_CACHE_TTL_MINUTES': '240',
        },
        'disable_env': {
            'WEATHERAPI_PER_RUN_MAX': '0',
            'WEATHERAPI_MAX_HTTP_REQUESTS_PER_RUN': '0',
        },
    },
    'openweathermap': {
        'per_run_max': 8,
        'safe_daily_budget': 240,
        'env': {
            'OPENWEATHERMAP_PER_RUN_MAX': '8',
            'OPENWEATHERMAP_MAX_HTTP_REQUESTS_PER_RUN': '8',
            'WEATHER_OPENWEATHERMAP_FALLBACK_ENABLED': 'true',
        },
        'disable_env': {
            'OPENWEATHERMAP_PER_RUN_MAX': '0',
            'OPENWEATHERMAP_MAX_HTTP_REQUESTS_PER_RUN': '0',
        },
    },
    'sportsbook_api': {
        'per_run_max': 2,
        'safe_daily_budget': 20,
        'min_spacing_minutes': 120,
        'allowed_msk_hours': ALL_HOURS,
        'env': {
            'RAPIDAPI_SPORTSBOOK_DAILY_LIMIT': '20',
            'RAPIDAPI_SPORTSBOOK_PER_RUN_MAX': '2',
            'RAPIDAPI_DISCOVERY_SPORTSBOOK_MAX_CALLS': '2',
        },
    },
    'meteostat': {
        'per_run_max': 2,
        'safe_daily_budget': 12,
        'safe_monthly_budget': 240,
        'min_spacing_minutes': 120,
        'allowed_msk_hours': ALL_HOURS,
        'env': {
            'RAPIDAPI_METEOSTAT_PER_RUN_MAX': '2',
            'METEOSTAT_MAX_HTTP_REQUESTS_PER_RUN': '2',
        },
    },
    'oddsfeed': {
        'per_run_max': 2,
        'safe_daily_budget': 12,
        'safe_monthly_budget': 240,
        'min_spacing_minutes': 120,
        'allowed_msk_hours': ALL_HOURS,
        'env': {
            'RAPIDAPI_ODDS_FEED_DAILY_LIMIT': '12',
            'RAPIDAPI_ODDS_FEED_PER_RUN_MAX': '2',
            'RAPIDAPI_DISCOVERY_ODDS_FEED_MAX_CALLS': '2',
        },
    },
    'freeapilivefootball': {
        'per_run_max': 1,
        'safe_daily_budget': 3,
        'safe_monthly_budget': 90,
        'min_spacing_minutes': 240,
        'allowed_msk_hours': ALL_HOURS,
        'env': {
            'RAPIDAPI_FREE_FOOTBALL_DAILY_LIMIT': '3',
            'RAPIDAPI_FREE_FOOTBALL_PER_RUN_MAX': '1',
            'RAPIDAPI_DISCOVERY_FREE_FOOTBALL_MAX_CALLS': '1',
        },
    },
    'sportapi': {
        'per_run_max': 2,
        'safe_daily_budget': 24,
        'min_spacing_minutes': 120,
        'allowed_msk_hours': ALL_HOURS,
        'env': {
            'RAPIDAPI_SPORTAPI7_DAILY_LIMIT': '24',
            'RAPIDAPI_SPORTAPI7_PER_RUN_MAX': '2',
            'RAPIDAPI_DISCOVERY_SPORTAPI7_MAX_CALLS': '2',
        },
    },
}


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


def env_truthy(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == '':
        return default
    return str(raw).strip().lower() in {'1', 'true', 'yes', 'on'}


def harizon_budget_recovery_enabled() -> bool:
    return (
        env_truthy('HARIZON_IGNORE_STALE_PROVIDER_BUDGET')
        or env_truthy('DAY_INVENTORY_COVERAGE_MAX_REBUILD')
        or str(os.getenv('HARIZON_RUNTIME_POLICY_VERSION') or '').strip() == 'harizon-runtime-policy-v1'
    )


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
    chunks: list[str] = []
    for path in [
        ROOT / '.data' / 'exports' / 'latest-provider-request-budget.json',
        ROOT / '.data' / 'exports' / 'latest-provider-request-usage.json',
        ROOT / '.data' / 'exports' / 'latest-rapidapi-provider-probe.json',
        ROOT / 'artifacts' / 'controlled-fallback-report.json',
        ROOT / '.logs' / 'debug-last-run.json',
    ]:
        try:
            if path.exists() and path.is_file():
                chunks.append(path.read_text(encoding='utf-8', errors='ignore')[:200000])
        except Exception:
            pass
    return '\n'.join(chunks).lower()


def merge_dict(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = merge_dict(dict(merged.get(key) or {}), value)
        else:
            merged[key] = value
    return merged


def maximize_free_providers(policy: dict[str, Any]) -> dict[str, Any]:
    if str(os.getenv('ALL_SOURCES_FREE_MAXIMIZE', 'true')).strip().lower() in {'0', 'false', 'no', 'off'}:
        return policy
    providers = policy.get('providers') if isinstance(policy.get('providers'), dict) else {}
    upgraded: dict[str, Any] = dict(policy)
    upgraded_providers: dict[str, Any] = dict(providers)
    for name, override in FREE_PROVIDER_OVERRIDES.items():
        current = upgraded_providers.get(name)
        if not isinstance(current, dict):
            continue
        upgraded_providers[name] = merge_dict(current, override)
    upgraded['providers'] = upgraded_providers
    version = str(upgraded.get('version') or 'provider-budget')
    if 'free-max' not in version:
        upgraded['version'] = f'{version}-free-max-v1'
    notes = list(upgraded.get('notes') or []) if isinstance(upgraded.get('notes'), list) else []
    notes.append('Free-source maximize mode raises per-run budgets and context caps for free providers based on RULES.txt quotas.')
    upgraded['notes'] = notes
    return upgraded


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
        'daily_budget': as_int(cfg.get('safe_daily_budget'), 0),
        'monthly_budget': as_int(cfg.get('safe_monthly_budget'), 0),
    }

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

    if is_manual and cfg.get('manual_enabled') is False:
        decision['reason'] = 'manual_disabled_by_policy'
        return decision
    if is_push and cfg.get('push_enabled') is False:
        decision['reason'] = 'push_disabled_by_policy'
        return decision

    allowed_hours = cfg.get('allowed_msk_hours')
    if isinstance(allowed_hours, list) and not is_manual:
        hours = {as_int(x, -1) for x in allowed_hours}
        if local_hour not in hours:
            decision['reason'] = f'slot_not_allowed:{local_hour:02d}MSK'
            return decision

    min_spacing = as_int(cfg.get('min_spacing_minutes'), 0)
    last_grant = parse_dt(row.get('last_grant_at'))
    if last_grant and min_spacing > 0:
        elapsed = (now - last_grant).total_seconds() / 60.0
        if elapsed < min_spacing:
            decision['reason'] = f'spacing_active:{elapsed:.1f}m/{min_spacing}m'
            return decision

    per_run = as_int(cfg.get('per_run_max'), 0)
    if is_push:
        per_run = min(per_run, as_int(cfg.get('push_per_run_max'), 0))
    if is_manual and 'manual_per_run_max' in cfg:
        per_run = as_int(cfg.get('manual_per_run_max'), per_run)

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
        if (
            harizon_budget_recovery_enabled()
            and name in HARIZON_CRITICAL_PROVIDERS
            and decision['reason'].startswith('daily_budget_exhausted:')
            and per_run > 0
        ):
            grant = max(1, min(per_run, HARIZON_RECOVERY_GRANTS.get(name, per_run)))
            decision.update({
                'grant': grant,
                'reason': f'harizon_recovery_after_{decision["reason"]}',
                'daily_remaining_after': None,
                'monthly_remaining_after': max(0, monthly_remaining - grant) if monthly_budget > 0 else None,
                'stale_daily_budget_bypassed': True,
            })
            row['last_grant_at'] = now.isoformat()
            row['last_grant'] = grant
            row['last_decision_reason'] = decision['reason']
            add_usage(row, 'daily', dkey, grant)
            add_usage(row, 'monthly', mkey, grant)
            return decision
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
        for key in cfg.get('secret_env_keys') or []:
            env[str(key)] = ''
    else:
        env = dict(cfg.get('env') or {})
    prefix = str(decision['provider']).upper().replace('-', '_')
    env[f'{prefix}_REQUEST_BUDGET_GRANTED'] = str(grant)
    env[f'{prefix}_REQUEST_BUDGET_REASON'] = str(decision.get('reason') or '')
    env.setdefault(f'{prefix}_MAX_HTTP_REQUESTS_PER_RUN', str(grant))
    return {str(k): str(v) for k, v in env.items()}


def main() -> int:
    policy = load_json(POLICY_PATH, {})
    if not isinstance(policy, dict):
        policy = {}
    policy = maximize_free_providers(policy)
    state = load_json(STATE_PATH, {})
    if not isinstance(state, dict):
        state = {}
    now = now_utc()
    recent_text = collect_recent_text()
    providers = policy.get('providers') if isinstance(policy.get('providers'), dict) else {}

    version = str(policy.get('version') or 'provider-budget-free-max')
    all_env: dict[str, str] = {
        'PROVIDER_REQUEST_BUDGET_VERSION': version,
        'PROVIDER_REQUEST_BUDGET_APPLIED': 'true',
        'PROVIDER_REQUEST_BUDGET_SLOT_MSK': current_slot_label(now),
        'ALL_SOURCES_FREE_MAXIMIZE': str(os.getenv('ALL_SOURCES_FREE_MAXIMIZE', 'true')).lower(),
    }
    deleted_env = policy.get('deleted_provider_env') if isinstance(policy.get('deleted_provider_env'), dict) else {}
    all_env.update({str(k): str(v) for k, v in deleted_env.items()})

    decisions: list[dict[str, Any]] = []
    for name, cfg in providers.items():
        if not isinstance(cfg, dict):
            continue
        row = provider_state(state, str(name))
        decision = decide_provider(str(name), cfg, row, now, recent_text)
        decisions.append(decision)
        all_env.update(build_env_for_decision(cfg, decision))

    append_github_env(all_env)
    state['version'] = version
    state['updated_at'] = now.isoformat()
    state['free_source_maximize'] = str(os.getenv('ALL_SOURCES_FREE_MAXIMIZE', 'true')).strip().lower() not in {'0', 'false', 'no', 'off'}
    write_json(STATE_PATH, state)
    export = {
        'version': version,
        'event': os.getenv('GITHUB_EVENT_NAME') or '',
        'utc_now': now.isoformat(),
        'msk_now': now.astimezone(MSK).isoformat(),
        'slot_msk': current_slot_label(now),
        'is_watchdog_slot': is_watchdog_slot(now),
        'deleted_providers': list((policy.get('deleted_providers') or [])),
        'free_source_maximize': state['free_source_maximize'],
        'decisions': decisions,
        'env_written_count': len(all_env),
        'notes': [
            'api-football remains removed from active runtime and its env values are blanked.',
            'Free-source maximize mode is active and raises budgets/context caps for free providers from RULES.txt quotas.',
            'Providers with explicit monthly cooldowns, such as OddsPapi after REQUEST_LIMIT_EXCEEDED, remain cooldown-skipped until reset.',
        ],
    }
    write_json(EXPORT_PATH, export)
    print(json.dumps(export, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
