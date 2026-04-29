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
    if src != original:
        PATH.write_text(src, encoding='utf-8')
        print(f'patched: {PATH}')
    else:
        print(f'already patched or no changes: {PATH}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
