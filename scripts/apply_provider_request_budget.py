from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path('.').resolve()
STATE_PATH = ROOT / '.data' / 'provider_request_budget_state.json'
EXPORT_PATH = ROOT / '.data' / 'exports' / 'latest-provider-request-budget.json'
GITHUB_ENV = os.getenv('GITHUB_ENV')
UTC = timezone.utc
MSK = ZoneInfo(os.getenv('APP_TIMEZONE') or os.getenv('TZ') or 'Europe/Moscow')
POLICY_VERSION = 'v19-near-window-useful-requests'

# This layer is deliberately conservative: it spends requests on providers that
# currently produce usable odds/context, and hard-disables providers that only
# return unparseable data. Runtime quality guards are not relaxed here.
PROVIDERS: dict[str, dict[str, Any]] = {
    'odds_api_io': {
        'grant': 200,
        'env': {
            'ENABLE_ODDS_API_IO': 'true',
            'ODDS_API_IO_ENABLED': 'true',
            'ODDS_API_IO_PER_RUN_MAX': '200',
            'ODDS_API_IO_MAX_HTTP_REQUESTS_PER_RUN': '200',
            'ODDS_API_IO_ACCOUNT1_PER_RUN_MAX': '100',
            'ODDS_API_IO_ACCOUNT2_PER_RUN_MAX': '100',
            'ODDS_API_IO_PAGE_LIMIT': '100',
            'ODDS_API_IO_MAX_EVENT_PAGES_PER_SPORT': '36',
            'MAX_MATCHES_FOR_ODDS_FETCH': '520',
        },
    },
    'bzzoiro': {
        'grant': 1000,
        'env': {
            'ENABLE_BZZOIRO': 'true',
            'ENABLE_BZZOIRO_CONTEXT': 'true',
            'BZZOIRO_ENABLED': 'true',
            'BZZOIRO_PER_RUN_MAX': '1000',
            'BZZOIRO_MAX_HTTP_REQUESTS_PER_RUN': '1000',
            'BZZOIRO_CONTEXT_MATCH_LIMIT': os.getenv('HARIZON_BZZOIRO_CONTEXT_MATCH_LIMIT') or os.getenv('BZZOIRO_CONTEXT_MATCH_LIMIT') or '180',
            'BZZOIRO_MAX_PAGES': os.getenv('HARIZON_BZZOIRO_MAX_PAGES') or '120',
        },
    },
    'sstats': {
        'grant': 150,
        'env': {
            'ENABLE_SSTATS': 'true',
            'ENABLE_SSTATS_CONTEXT': 'true',
            'SSTATS_ENABLED': 'true',
            'SSTATS_TARGET_NEAR_MISS_FIRST': 'true',
            'SSTATS_PER_RUN_MAX': '150',
            'SSTATS_REQUESTS_MAX_PER_RUN': '150',
            'SSTATS_MAX_HTTP_REQUESTS_PER_RUN': '150',
            'SSTATS_CONTEXT_MATCH_LIMIT': os.getenv('HARIZON_SSTATS_CONTEXT_MATCH_LIMIT') or os.getenv('SSTATS_CONTEXT_MATCH_LIMIT') or '180',
            'SSTATS_LOOKBACK_DAYS': '45',
            'SSTATS_RECENT_MATCHES': '10',
        },
    },
    'football_data': {
        'grant': 8,
        'env': {
            'ENABLE_FOOTBALL_DATA_CONTEXT': 'true',
            'FOOTBALL_DATA_ENABLED': 'true',
            'FOOTBALL_DATA_PER_RUN_MAX': '8',
            'FOOTBALL_DATA_REQUESTS_MAX_PER_RUN': '8',
            'FOOTBALL_DATA_CONTEXT_MATCH_LIMIT': os.getenv('FOOTBALL_DATA_CONTEXT_MATCH_LIMIT') or '48',
        },
    },
    'thesportsdb': {
        'grant': 12,
        'env': {
            'ENABLE_THESPORTSDB_CONTEXT': 'true',
            'THESPORTSDB_CONTEXT_ENABLED': 'true',
            'THESPORTSDB_PER_RUN_MAX': '12',
            'THESPORTSDB_REQUESTS_MAX_PER_RUN': '12',
            'THESPORTSDB_MAX_HTTP_REQUESTS_PER_RUN': '12',
            'THESPORTSDB_CONTEXT_MATCH_LIMIT': os.getenv('THESPORTSDB_CONTEXT_MATCH_LIMIT') or '60',
        },
    },
    # SportLogic has repeatedly returned odds rows without parseable price fields.
    # Grant zero until parser/provider payload is fixed; this prevents 30-40 wasted calls every run.
    'sportlogic': {
        'grant': 0,
        'reason': 'disabled_by_payload_shape:missing_or_invalid_price',
        'env': {
            'ENABLE_SPORTLOGIC': 'false',
            'SPORTLOGIC_ENABLED': 'false',
            'SPORTLOGIC_PER_RUN_MAX': '0',
            'SPORTLOGIC_MATCH_LIMIT': '0',
            'SPORTLOGIC_ODDS_MATCH_LIMIT': '0',
            'SPORTLOGIC_MAX_HTTP_REQUESTS_PER_RUN': '0',
            'SPORTLOGIC_ODDS_DISABLED_REASON': 'missing_or_invalid_price_payload',
        },
    },
    'weatherapi': {
        'grant': 12,
        'env': {
            'ENABLE_WEATHERAPI': 'true',
            'WEATHERAPI_ENABLED': 'true',
            'WEATHERAPI_PER_RUN_MAX': '12',
            'WEATHERAPI_MAX_HTTP_REQUESTS_PER_RUN': '12',
            'WEATHER_CONTEXT_MATCH_LIMIT': os.getenv('WEATHER_CONTEXT_MATCH_LIMIT') or '24',
            'WEATHER_CACHE_TTL_MINUTES': '240',
        },
    },
    'openweathermap': {
        'grant': 8,
        'env': {
            'ENABLE_OPENWEATHERMAP': 'true',
            'OPENWEATHERMAP_ENABLED': 'true',
            'OPENWEATHERMAP_PER_RUN_MAX': '8',
            'OPENWEATHERMAP_MAX_HTTP_REQUESTS_PER_RUN': '8',
            'WEATHER_OPENWEATHERMAP_FALLBACK_ENABLED': 'true',
        },
    },
    # Paid / fragile / spacing-limited providers stay gated.
    'oddspapi': {'grant': 0, 'reason': 'cooldown_active:fatal_pattern:REQUEST_LIMIT_EXCEEDED', 'env': {'ODDSPAPI_PER_RUN_MAX': '0'}},
    'futrixmetrics': {'grant': 0, 'reason': 'spacing_active:60m', 'env': {'FUTRIXMETRICS_PER_RUN_MAX': '0'}},
    'gnews': {'grant': 0, 'reason': 'spacing_active:60m', 'env': {'GNEWS_PER_RUN_MAX': '0', 'GNEWS_MAX_HTTP_REQUESTS_PER_RUN': '0'}},
    'meteostat': {'grant': 0, 'reason': 'spacing_active:120m', 'env': {'RAPIDAPI_METEOSTAT_PER_RUN_MAX': '0'}},
    'oddsfeed': {'grant': 0, 'reason': 'spacing_active:120m', 'env': {'RAPIDAPI_ODDS_FEED_PER_RUN_MAX': '0'}},
    'sportsbook_api': {'grant': 0, 'reason': 'spacing_active:120m', 'env': {'RAPIDAPI_SPORTSBOOK_PER_RUN_MAX': '0'}},
    'sportapi': {'grant': 0, 'reason': 'spacing_active:120m', 'env': {'RAPIDAPI_SPORTAPI7_PER_RUN_MAX': '0'}},
}

BASE_ENV = {
    'PROVIDER_REQUEST_BUDGET_VERSION': POLICY_VERSION,
    'PROVIDER_REQUEST_BUDGET_APPLIED': 'true',
    'PROVIDER_REQUEST_BUDGET_MODE': os.getenv('PROVIDER_REQUEST_BUDGET_MODE') or 'per_run_only',
    'PROVIDER_REQUEST_BUDGET_DISABLE_DAILY_MONTHLY': 'true',
    'ALL_SOURCES_FREE_MAXIMIZE': str(os.getenv('ALL_SOURCES_FREE_MAXIMIZE', 'true')).lower(),
    # Useful-request policy.
    'PUBLISH_WINDOW_HOURS': os.getenv('PUBLISH_WINDOW_HOURS') or '12',
    'CONTEXT_ENRICHMENT_REQUIRES_OFFERS': 'true',
    'CONTEXT_ENRICHMENT_MATCH_LIMIT': os.getenv('CONTEXT_ENRICHMENT_MATCH_LIMIT') or '180',
    'PREMIUM_CONTEXT_SHORTLIST_LIMIT': os.getenv('PREMIUM_CONTEXT_SHORTLIST_LIMIT') or '72',
    'DAY_INVENTORY_NEAR_WINDOW_HOURS': os.getenv('DAY_INVENTORY_NEAR_WINDOW_HOURS') or '12',
    'DAY_INVENTORY_NEAR_WINDOW_PRIORITY': 'true',
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
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')


def append_github_env(values: dict[str, str]) -> None:
    if not GITHUB_ENV:
        for key in sorted(values):
            print(f'{key}={values[key]}')
        return
    with open(GITHUB_ENV, 'a', encoding='utf-8') as fh:
        for key in sorted(values):
            fh.write(f'{key}={values[key]}\n')


def main() -> int:
    now = datetime.now(UTC)
    state = load_json(STATE_PATH, {})
    if not isinstance(state, dict):
        state = {}
    decisions: list[dict[str, Any]] = []
    env: dict[str, str] = dict(BASE_ENV)
    for provider, cfg in PROVIDERS.items():
        grant = int(cfg.get('grant') or 0)
        reason = str(cfg.get('reason') or ('granted' if grant > 0 else 'disabled_by_policy'))
        prefix = provider.upper().replace('-', '_')
        for key, value in dict(cfg.get('env') or {}).items():
            env[str(key)] = str(value)
        env[f'{prefix}_REQUEST_BUDGET_GRANTED'] = str(grant)
        env[f'{prefix}_REQUEST_BUDGET_REASON'] = reason
        env.setdefault(f'{prefix}_MAX_HTTP_REQUESTS_PER_RUN', str(grant))
        decisions.append({
            'provider': provider,
            'grant': grant,
            'reason': reason,
            'slot': now.astimezone(MSK).strftime('%H:%M MSK'),
        })
    append_github_env(env)
    state.update({
        'version': POLICY_VERSION,
        'updated_at': now.isoformat(),
        'last_decisions': decisions,
    })
    write_json(STATE_PATH, state)
    export = {
        'version': POLICY_VERSION,
        'event': os.getenv('GITHUB_EVENT_NAME') or '',
        'utc_now': now.isoformat(),
        'msk_now': now.astimezone(MSK).isoformat(),
        'slot_msk': now.astimezone(MSK).strftime('%H:%M MSK'),
        'decisions': decisions,
        'env_written_count': len(env),
        'notes': [
            'Near-window/useful-request policy is active: context requests are focused on priced 6-12h candidates.',
            'SportLogic is hard-disabled because recent payloads had odds rows but no valid price fields.',
            'Daily/monthly request accounting is intentionally bypassed; only per-run useful caps are used.',
        ],
    }
    write_json(EXPORT_PATH, export)
    print(json.dumps(export, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
