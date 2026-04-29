from __future__ import annotations

from pathlib import Path

PATH = Path('scripts/apply_provider_request_budget.py')

REPLACEMENTS = {
    "'WEATHERAPI_MAX_HTTP_REQUESTS_PER_RUN': '12'": "'WEATHERAPI_MAX_HTTP_REQUESTS_PER_RUN': '8'",
    "'WEATHER_CONTEXT_MATCH_LIMIT': '20'": "'WEATHER_CONTEXT_MATCH_LIMIT': '12'",
    "'OPENWEATHERMAP_MAX_HTTP_REQUESTS_PER_RUN': '8'": "'OPENWEATHERMAP_MAX_HTTP_REQUESTS_PER_RUN': '4'",
    "'RAPIDAPI_SPORTSBOOK_PROBE_ENABLED': 'true'": "'RAPIDAPI_SPORTSBOOK_PROBE_ENABLED': 'false'",
    "'RAPIDAPI_FREE_FOOTBALL_PROBE_ENABLED': 'true'": "'RAPIDAPI_FREE_FOOTBALL_PROBE_ENABLED': 'false'",
    "'RAPIDAPI_SPORTAPI7_PROBE_ENABLED': 'true'": "'RAPIDAPI_SPORTAPI7_PROBE_ENABLED': 'false'",
    "'RAPIDAPI_ODDS_FEED_PROBE_ENABLED': 'true'": "'RAPIDAPI_ODDS_FEED_PROBE_ENABLED': 'false'",
    "'RAPIDAPI_DISCOVERY_SPORTSBOOK_MAX_CALLS': '2'": "'RAPIDAPI_DISCOVERY_SPORTSBOOK_MAX_CALLS': '0'",
    "'RAPIDAPI_DISCOVERY_SPORTSBOOK_MAX_CALLS': '1'": "'RAPIDAPI_DISCOVERY_SPORTSBOOK_MAX_CALLS': '0'",
    "'RAPIDAPI_DISCOVERY_FREE_FOOTBALL_MAX_CALLS': '1'": "'RAPIDAPI_DISCOVERY_FREE_FOOTBALL_MAX_CALLS': '0'",
    "'RAPIDAPI_DISCOVERY_SPORTAPI7_MAX_CALLS': '2'": "'RAPIDAPI_DISCOVERY_SPORTAPI7_MAX_CALLS': '0'",
    "'RAPIDAPI_DISCOVERY_SPORTAPI7_MAX_CALLS': '1'": "'RAPIDAPI_DISCOVERY_SPORTAPI7_MAX_CALLS': '0'",
    "'RAPIDAPI_DISCOVERY_ODDS_FEED_MAX_CALLS': '2'": "'RAPIDAPI_DISCOVERY_ODDS_FEED_MAX_CALLS': '0'",
    "'RAPIDAPI_DISCOVERY_ODDS_FEED_MAX_CALLS': '1'": "'RAPIDAPI_DISCOVERY_ODDS_FEED_MAX_CALLS': '0'",
}

HELPER_BLOCK = """

def env_true(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == '':
        return default
    return str(raw).strip().lower() in {'1', 'true', 'yes', 'on'}


def apply_rapidapi_runtime_budget_overrides(name: str, cfg: dict[str, Any], row: dict[str, Any]) -> dict[str, Any]:
    provider = str(name)
    patched = dict(cfg)

    if provider == 'oddspapi' and env_true('ODDSPAPI_RAPIDAPI_ENABLED', False):
        row.pop('cooldown_until', None)
        row.pop('cooldown_reason', None)
        patched['min_spacing_minutes'] = 0
        patched['allowed_msk_hours'] = list(range(24))
        patched['per_run_max'] = max(1, as_int(patched.get('per_run_max'), 1))
        env = dict(patched.get('env') or {})
        env.update({
            'ENABLE_ODDSPAPI': 'true',
            'ODDSPAPI_ENABLED': 'true',
            'ODDSPAPI_RAPIDAPI_ENABLED': 'true',
            'ODDSPAPI_RAPIDAPI_HOST': os.getenv('ODDSPAPI_RAPIDAPI_HOST') or 'odds-api1.p.rapidapi.com',
            'ODDSPAPI_RAPIDAPI_USE_QUERY_API_KEY': os.getenv('ODDSPAPI_RAPIDAPI_USE_QUERY_API_KEY') or 'false',
            'ODDSPAPI_BASE_URL': os.getenv('ODDSPAPI_BASE_URL') or 'https://odds-api1.p.rapidapi.com/v4',
            'ODDSPAPI_PER_RUN_MAX': str(max(1, as_int(patched.get('per_run_max'), 1))),
        })
        patched['env'] = env

    if provider in {'sportsbook_api', 'freeapilivefootball', 'sportapi', 'allsportsapi', 'oddsfeed'} and not env_true('RAPIDAPI_PRODUCTION_DIAGNOSTIC_PROBES_ENABLED', False):
        patched['enabled'] = False
        patched['per_run_max'] = 0
        patched['safe_daily_budget'] = 0
        patched['safe_monthly_budget'] = 0
        disable_env = dict(patched.get('disable_env') or {})
        disable_env.update({
            'ENABLE_RAPIDAPI_ODDS_BRIDGE': 'false',
            'RAPIDAPI_ODDS_MAX_HTTP_REQUESTS_PER_RUN': '0',
            'RAPIDAPI_ODDS_MATCH_LIMIT': '0',
            'RAPIDAPI_ODDS_CACHE_TTL_MINUTES': '240',
        })
        if provider == 'sportsbook_api':
            disable_env.update({
                'RAPIDAPI_SPORTSBOOK_PROBE_ENABLED': 'false',
                'RAPIDAPI_SPORTSBOOK_DAILY_LIMIT': '0',
                'RAPIDAPI_SPORTSBOOK_PER_RUN_MAX': '0',
                'RAPIDAPI_DISCOVERY_SPORTSBOOK_MAX_CALLS': '0',
            })
        elif provider == 'freeapilivefootball':
            disable_env.update({
                'RAPIDAPI_FREE_FOOTBALL_PROBE_ENABLED': 'false',
                'RAPIDAPI_FREE_FOOTBALL_DAILY_LIMIT': '0',
                'RAPIDAPI_FREE_FOOTBALL_PER_RUN_MAX': '0',
                'RAPIDAPI_DISCOVERY_FREE_FOOTBALL_MAX_CALLS': '0',
            })
        elif provider == 'sportapi':
            disable_env.update({
                'RAPIDAPI_SPORTAPI7_PROBE_ENABLED': 'false',
                'RAPIDAPI_SPORTAPI7_DAILY_LIMIT': '0',
                'RAPIDAPI_SPORTAPI7_PER_RUN_MAX': '0',
                'RAPIDAPI_DISCOVERY_SPORTAPI7_MAX_CALLS': '0',
            })
        elif provider == 'oddsfeed':
            disable_env.update({
                'RAPIDAPI_ODDS_FEED_PROBE_ENABLED': 'false',
                'RAPIDAPI_ODDS_FEED_DAILY_LIMIT': '0',
                'RAPIDAPI_ODDS_FEED_PER_RUN_MAX': '0',
                'RAPIDAPI_DISCOVERY_ODDS_FEED_MAX_CALLS': '0',
            })
        patched['disable_env'] = disable_env
    return patched
"""

LOOP_OLD = """        row = provider_state(state, str(name))
        decision = decide_provider(str(name), cfg, row, now, recent_text)
"""

LOOP_NEW = """        row = provider_state(state, str(name))
        cfg = apply_rapidapi_runtime_budget_overrides(str(name), cfg, row)
        decision = decide_provider(str(name), cfg, row, now, recent_text)
"""


def main() -> int:
    if not PATH.exists():
        print(f'skip: {PATH} not found')
        return 0
    src = PATH.read_text(encoding='utf-8')
    original = src
    for old, new in REPLACEMENTS.items():
        src = src.replace(old, new)
    marker = "'WEATHER_CACHE_TTL_MINUTES': '240',"
    if marker in src and "'WEATHER_ALLOW_TEAM_NAME_FALLBACK': 'false'" not in src:
        src = src.replace(
            marker,
            marker + "\n            'WEATHER_SHORTLIST_ONLY': 'true',\n            'WEATHER_ALLOW_TEAM_NAME_FALLBACK': 'false',",
            1,
        )
    env_marker = "'ALL_SOURCES_FREE_MAXIMIZE': str(os.getenv('ALL_SOURCES_FREE_MAXIMIZE', 'true')).lower(),"
    if env_marker in src and "'ENABLE_RAPIDAPI_ODDS_BRIDGE': 'false'" not in src:
        src = src.replace(
            env_marker,
            env_marker + "\n        'ENABLE_RAPIDAPI_ODDS_BRIDGE': 'false',\n        'RAPIDAPI_ODDS_MAX_HTTP_REQUESTS_PER_RUN': '0',\n        'RAPIDAPI_ODDS_MATCH_LIMIT': '0',",
            1,
        )
    if 'def apply_rapidapi_runtime_budget_overrides(' not in src:
        insert_before = '\ndef main() -> int:\n'
        if insert_before in src:
            src = src.replace(insert_before, HELPER_BLOCK + insert_before, 1)
        else:
            print('warn: main marker not found for rapidapi budget helper')
    if LOOP_OLD in src and 'apply_rapidapi_runtime_budget_overrides(str(name), cfg, row)' not in src:
        src = src.replace(LOOP_OLD, LOOP_NEW, 1)
    if src != original:
        PATH.write_text(src, encoding='utf-8')
        print(f'patched: {PATH}')
    else:
        print(f'already patched or no changes: {PATH}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
