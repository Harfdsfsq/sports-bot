from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path('.').resolve()
UTC = timezone.utc
RULES_PATH = ROOT / 'RULES.txt'
POLICY_PATH = ROOT / 'config' / 'provider_request_budget.json'
OUT = ROOT / '.data' / 'exports' / 'latest-rules-api-budget-policy.json'
GITHUB_ENV = os.getenv('GITHUB_ENV')


def load_json(path: Path, default: Any) -> Any:
    try:
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


def merge_provider(provider: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    merged = dict(provider)
    for key, value in patch.items():
        if key == 'env':
            env = dict(merged.get('env') or {})
            env.update({str(k): str(v) for k, v in dict(value).items()})
            merged['env'] = env
        elif key == 'disable_env':
            env = dict(merged.get('disable_env') or {})
            env.update({str(k): str(v) for k, v in dict(value).items()})
            merged['disable_env'] = env
        elif isinstance(value, dict) and isinstance(merged.get(key), dict):
            nested = dict(merged.get(key) or {})
            nested.update(value)
            merged[key] = nested
        else:
            merged[key] = value
    return merged


# Distribution assumes 12 scheduled primary runs/day. Values are intentionally
# below public hard limits from RULES.txt while preventing useful providers from
# being exhausted by the evening.
RULES_BUDGETS: dict[str, dict[str, Any]] = {
    'odds_api_io': {
        'per_run_max': 24,
        'safe_daily_budget': 288,
        'min_spacing_minutes': 0,
        'limit': {'requests_per_hour': 100, 'bookmakers': 2, 'rules_source': 'RULES.txt'},
        'env': {
            'ENABLE_ODDS_API_IO': 'true',
            'ODDS_API_IO_ENABLED': 'true',
            'ODDS_API_IO_PER_RUN_MAX': '24',
            'ODDS_API_IO_PAGE_LIMIT': '100',
            'ODDS_API_IO_MAX_EVENT_PAGES_PER_SPORT': '18',
            'MAX_MATCHES_FOR_ODDS_FETCH': '160',
        },
    },
    'bzzoiro': {
        'per_run_max': 240,
        'safe_daily_budget': 100000,
        'min_spacing_minutes': 0,
        'limit': {'free_forever_no_rate_limit': True, 'rules_source': 'RULES.txt'},
        'env': {
            'ENABLE_BZZOIRO': 'true',
            'ENABLE_BZZOIRO_CONTEXT': 'true',
            'BZZOIRO_ENABLED': 'true',
            'BZZOIRO_PER_RUN_MAX': '240',
            'BZZOIRO_CONTEXT_MATCH_LIMIT': '240',
        },
    },
    'sstats': {
        'per_run_max': 48,
        'safe_daily_budget': 576,
        'min_spacing_minutes': 0,
        'limit': {'requests_per_minute': 150, 'rules_source': 'RULES.txt'},
        'env': {
            'ENABLE_SSTATS': 'true',
            'ENABLE_SSTATS_CONTEXT': 'true',
            'SSTATS_ENABLED': 'true',
            'SSTATS_PER_RUN_MAX': '48',
            'SSTATS_REQUESTS_MAX_PER_RUN': '48',
            'SSTATS_CONTEXT_MATCH_LIMIT': '120',
            'SSTATS_LOOKBACK_DAYS': '30',
            'SSTATS_RECENT_MATCHES': '10',
        },
    },
    'football_data': {
        'per_run_max': 10,
        'safe_daily_budget': 120,
        'min_spacing_minutes': 2,
        'limit': {
            'requests_per_minute_registered': 10,
            'unauthenticated_requests_per_24h': 100,
            'rules_source': 'RULES.txt',
        },
        'env': {
            'ENABLE_FOOTBALL_DATA': 'true',
            'ENABLE_FOOTBALL_DATA_CONTEXT': 'true',
            'FOOTBALL_DATA_ENABLED': 'true',
            'FOOTBALL_DATA_PER_RUN_MAX': '10',
            'FOOTBALL_DATA_REQUESTS_MAX_PER_RUN': '10',
            'FOOTBALL_DATA_CONTEXT_MATCH_LIMIT': '120',
        },
    },
    'thesportsdb': {
        'per_run_max': 24,
        'safe_daily_budget': 288,
        'min_spacing_minutes': 0,
        'limit': {'requests_per_minute': 30, 'rules_source': 'RULES.txt'},
        'env': {
            'ENABLE_THESPORTSDB': 'true',
            'ENABLE_THESPORTSDB_CONTEXT': 'true',
            'THESPORTSDB_ENABLED': 'true',
            'THESPORTSDB_PER_RUN_MAX': '24',
            'THESPORTSDB_REQUESTS_MAX_PER_RUN': '24',
            'THESPORTSDB_CONTEXT_MATCH_LIMIT': '120',
        },
    },
    'openfootball_public': {
        'per_run_max': 12,
        'safe_daily_budget': 240,
        'min_spacing_minutes': 0,
        'limit': {'public_no_key': True, 'rules_source': 'internal_public_source'},
        'env': {
            'ENABLE_OPENFOOTBALL_CONTEXT': 'true',
            'OPENFOOTBALL_ENABLED': 'true',
            'OPENFOOTBALL_CONTEXT_MATCH_LIMIT': '180',
            'OPENFOOTBALL_MAX_HTTP_REQUESTS_PER_RUN': '12',
            'OPENFOOTBALL_SKIP_404_CACHE_TTL_HOURS': '24',
        },
    },
    'newsapi_currents': {
        'per_run_max': 4,
        'safe_daily_budget': 48,
        'min_spacing_minutes': 60,
        'allowed_msk_hours': list(range(24)),
        'limit': {'currents_requests_per_day': 1000, 'newsapi_requests_per_day': 100, 'rules_source': 'RULES.txt'},
        'env': {
            'ENABLE_NEWSAPI': 'true',
            'ENABLE_NEWSAPI_CONTEXT': 'true',
            'NEWSAPI_ENABLED': 'true',
            'NEWSAPI_PER_RUN_MAX': '1',
            'NEWSAPI_MAX_HTTP_REQUESTS_PER_RUN': '1',
            'NEWSAPI_MATCH_LIMIT': '2',
            'NEWS_CONTEXT_MATCH_LIMIT': '8',
            'CURRENTS_NEWS_PER_RUN_MAX': '4',
            'CURRENTS_NEWS_MAX_HTTP_REQUESTS_PER_RUN': '4',
            'CURRENTS_MATCH_LIMIT': '8',
            'NEWS_CONTEXT_CACHE_TTL_MINUTES': '180',
        },
    },
    'gnews': {
        'per_run_max': 2,
        'safe_daily_budget': 36,
        'min_spacing_minutes': 60,
        'allowed_msk_hours': list(range(24)),
        'limit': {'requests_per_day': 100, 'rules_source': 'RULES.txt'},
        'env': {
            'ENABLE_GNEWS': 'true',
            'ENABLE_GNEWS_CONTEXT': 'true',
            'GNEWS_ENABLED': 'true',
            'GNEWS_PER_RUN_MAX': '2',
            'GNEWS_MAX_HTTP_REQUESTS_PER_RUN': '2',
            'GNEWS_CONTEXT_MATCH_LIMIT': '8',
            'GNEWS_MATCH_LIMIT': '4',
        },
    },
    'allsportsapi': {
        'per_run_max': 1,
        'safe_daily_budget': 8,
        'min_spacing_minutes': 180,
        'allowed_msk_hours': [0, 6, 12, 18],
        'limit': {'unknown_free_trial': True, 'rules_source': 'RULES.txt'},
    },
    'oddspapi': {
        'per_run_max': 1,
        'safe_daily_budget': 4,
        'safe_monthly_budget': 220,
        'min_spacing_minutes': 720,
        'allowed_msk_hours': [9, 21],
        'limit': {'requests_per_month': 250, 'rules_source': 'RULES.txt'},
    },
    'futrixmetrics': {
        'per_run_max': 6,
        'safe_daily_budget': 120,
        'safe_monthly_budget': 4500,
        'min_spacing_minutes': 60,
        'allowed_msk_hours': list(range(24)),
        'limit': {'requests_per_month': 5000, 'rules_source': 'RULES.txt'},
        'env': {
            'ENABLE_FUTRIXMETRICS': 'true',
            'ENABLE_FUTRIXMETRICS_CONTEXT': 'true',
            'FUTRIXMETRICS_ENABLED': 'true',
            'FUTRIXMETRICS_PER_RUN_MAX': '6',
            'FUTRIXMETRICS_REQUESTS_MAX_PER_RUN': '6',
            'FUTRIXMETRICS_CONTEXT_MATCH_LIMIT': '16',
            'FUTRIXMETRICS_SHORTLIST_ONLY': 'false',
            'FUTRIXMETRICS_MIN_SPACING_MINUTES': '60',
        },
    },
    'weatherapi': {
        'per_run_max': 24,
        'safe_daily_budget': 1500,
        'safe_monthly_budget': 70000,
        'min_spacing_minutes': 0,
        'limit': {'requests_per_month': 100000, 'rules_source': 'RULES.txt'},
        'env': {
            'ENABLE_WEATHERAPI': 'true',
            'WEATHERAPI_ENABLED': 'true',
            'WEATHERAPI_PER_RUN_MAX': '24',
            'WEATHERAPI_MAX_HTTP_REQUESTS_PER_RUN': '24',
            'WEATHER_CONTEXT_ENABLED': 'true',
            'WEATHER_CONTEXT_MATCH_LIMIT': '40',
            'WEATHER_CACHE_TTL_MINUTES': '240',
        },
    },
    'openweathermap': {
        'per_run_max': 16,
        'safe_daily_budget': 480,
        'min_spacing_minutes': 0,
        'limit': {'requests_per_day': 1000, 'requests_per_minute': 60, 'rules_source': 'RULES.txt'},
        'env': {
            'ENABLE_OPENWEATHERMAP': 'true',
            'OPENWEATHERMAP_ENABLED': 'true',
            'OPENWEATHERMAP_PER_RUN_MAX': '16',
            'OPENWEATHERMAP_MAX_HTTP_REQUESTS_PER_RUN': '16',
            'WEATHER_OPENWEATHERMAP_FALLBACK_ENABLED': 'true',
        },
    },
    'sportsbook_api': {
        'per_run_max': 2,
        'safe_daily_budget': 36,
        'min_spacing_minutes': 120,
        'allowed_msk_hours': list(range(24)),
        'limit': {'requests_per_day': 50, 'rules_source': 'RULES.txt'},
        'env': {
            'RAPIDAPI_SPORTSBOOK_PROBE_ENABLED': 'true',
            'RAPIDAPI_SPORTSBOOK_DAILY_LIMIT': '36',
            'RAPIDAPI_SPORTSBOOK_PER_RUN_MAX': '2',
            'RAPIDAPI_DISCOVERY_SPORTSBOOK_MAX_CALLS': '2',
        },
    },
    'meteostat': {
        'per_run_max': 1,
        'safe_daily_budget': 12,
        'safe_monthly_budget': 360,
        'min_spacing_minutes': 120,
        'allowed_msk_hours': list(range(24)),
        'limit': {'requests_per_month': 500, 'rules_source': 'RULES.txt'},
    },
    'oddsfeed': {
        'per_run_max': 2,
        'safe_daily_budget': 12,
        'safe_monthly_budget': 360,
        'min_spacing_minutes': 240,
        'allowed_msk_hours': list(range(24)),
        'limit': {'requests_per_month': 500, 'rules_source': 'RULES.txt'},
        'env': {
            'RAPIDAPI_ODDS_FEED_PROBE_ENABLED': 'true',
            'RAPIDAPI_ODDS_FEED_DAILY_LIMIT': '12',
            'RAPIDAPI_ODDS_FEED_PER_RUN_MAX': '2',
            'RAPIDAPI_DISCOVERY_ODDS_FEED_MAX_CALLS': '2',
        },
    },
    'freeapilivefootball': {
        'per_run_max': 1,
        'safe_daily_budget': 3,
        'safe_monthly_budget': 90,
        'min_spacing_minutes': 480,
        'allowed_msk_hours': [0, 8, 16],
        'limit': {'requests_per_month': 100, 'rules_source': 'RULES.txt'},
    },
    'sportapi': {
        'per_run_max': 4,
        'safe_daily_budget': 48,
        'min_spacing_minutes': 120,
        'allowed_msk_hours': list(range(24)),
        'limit': {'requests_per_day': 100, 'rules_source': 'RULES.txt'},
        'env': {
            'RAPIDAPI_SPORTAPI7_PROBE_ENABLED': 'true',
            'RAPIDAPI_SPORTAPI7_DAILY_LIMIT': '48',
            'RAPIDAPI_SPORTAPI7_PER_RUN_MAX': '4',
            'RAPIDAPI_DISCOVERY_SPORTAPI7_MAX_CALLS': '4',
        },
    },
}


def main() -> int:
    policy = load_json(POLICY_PATH, {})
    if not isinstance(policy, dict):
        policy = {}
    providers = policy.get('providers') if isinstance(policy.get('providers'), dict) else {}
    patched: dict[str, Any] = {}
    missing: list[str] = []
    for name, patch in RULES_BUDGETS.items():
        current = providers.get(name)
        if not isinstance(current, dict):
            missing.append(name)
            continue
        providers[name] = merge_provider(current, patch)
        patched[name] = {
            'per_run_max': providers[name].get('per_run_max'),
            'safe_daily_budget': providers[name].get('safe_daily_budget'),
            'safe_monthly_budget': providers[name].get('safe_monthly_budget'),
            'min_spacing_minutes': providers[name].get('min_spacing_minutes'),
            'allowed_msk_hours': providers[name].get('allowed_msk_hours'),
            'env_subset': {
                key: value
                for key, value in dict(providers[name].get('env') or {}).items()
                if key.endswith('_PER_RUN_MAX')
                or key.endswith('_REQUESTS_MAX_PER_RUN')
                or key.endswith('_CONTEXT_MATCH_LIMIT')
                or key in {'MAX_MATCHES_FOR_ODDS_FETCH', 'WEATHER_CONTEXT_MATCH_LIMIT'}
            },
        }
    policy['providers'] = providers
    policy['version'] = 'v16-rules-api-budget-balanced-day-enrichment'
    policy['description'] = 'Runtime API budgets are derived from RULES.txt and distributed for 12 primary runs/day without early evening exhaustion.'
    notes = list(policy.get('notes') or []) if isinstance(policy.get('notes'), list) else []
    notes.append('RULES policy disables legacy FREE_PROVIDER_OVERRIDES so the old sstats/football_data/thesportsdb caps cannot overwrite this distribution.')
    notes.append('api-football remains disabled by project policy; SportAPI/API-Sports budget is kept under the 100/day rule.')
    policy['notes'] = notes
    write_json(POLICY_PATH, policy)

    env = {
        'RULES_API_BUDGET_POLICY_ACTIVE': 'true',
        'RULES_API_BUDGET_POLICY_VERSION': str(policy['version']),
        # apply_provider_request_budget.py has a legacy FREE_PROVIDER_OVERRIDES layer.
        # Disable it after writing RULES-based config, otherwise it would reintroduce
        # the old evening-exhaustion caps.
        'ALL_SOURCES_FREE_MAXIMIZE': 'false',
    }
    append_env(env)

    report = {
        'status': 'ok',
        'updated_at_utc': datetime.now(UTC).isoformat(),
        'rules_file_present': RULES_PATH.exists(),
        'policy_path': str(POLICY_PATH),
        'version': policy['version'],
        'patched_provider_count': len(patched),
        'missing_providers': missing,
        'patched': patched,
        'env': env,
        'distribution_notes': [
            'sstats raised from artificial 192/day to 576/day; RULES says 150 requests/minute.',
            'football_data raised to 120/day assuming registered key; still respects 10 requests/minute spacing.',
            'thesportsdb raised to 288/day; RULES says 30 requests/minute.',
            'news providers remain spaced to avoid noisy quota burn; weather is raised but still well below free monthly/day limits.',
            'monthly providers are capped below monthly quota: OddsPapi 220/250, FutrixMetrics 4500/5000, Meteostat/OddsFeed 360/500, FreeAPILiveFootballData 90/100.',
        ],
    }
    write_json(OUT, report)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
