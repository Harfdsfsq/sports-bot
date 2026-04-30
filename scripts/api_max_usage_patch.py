from __future__ import annotations

"""Aggressive-but-safe API usage patch for Harizon.

Applied from both root sitecustomize.py and scripts/sitecustomize.py before the
runner or workflow scripts execute.  The intent is to use high-value/free APIs
much harder while keeping explicit daily/monthly safeguards for quota-limited
sources.
"""

import json
import os
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


def _truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _setenv(key: str, value: Any, *, overwrite: bool = True) -> None:
    if not overwrite and os.getenv(key) not in (None, ""):
        return
    os.environ[key] = str(value)


def _alias_env(target: str, *aliases: str) -> None:
    if os.getenv(target):
        return
    for alias in aliases:
        value = os.getenv(alias)
        if value:
            os.environ[target] = value
            return


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return None


def _write_if_changed(path: Path, old: str | None, new: str | None) -> None:
    if old is None or new is None or new == old:
        return
    try:
        path.write_text(new, encoding="utf-8")
    except Exception:
        return


def _patch_text_file(rel_path: str, patcher) -> None:
    path = ROOT / rel_path
    old = _read(path)
    if old is None:
        return
    try:
        new = patcher(old)
    except Exception:
        return
    _write_if_changed(path, old, new)


def _merge_dict(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge_dict(dict(result[key]), value)
        else:
            result[key] = value
    return result


AGGRESSIVE_ENV: dict[str, str] = {
    # Global mode.
    "ALL_SOURCES_FREE_MAXIMIZE": "true",
    "PROVIDER_REQUEST_BUDGET_FORCE_FRESH": "true",

    # SStats: user-confirmed limit 150 requests/min.  Per run is capped at 150.
    "ENABLE_SSTATS": "true",
    "ENABLE_SSTATS_CONTEXT": "true",
    "SSTATS_ENABLED": "true",
    "SSTATS_PER_RUN_MAX": "150",
    "SSTATS_REQUESTS_MAX_PER_RUN": "150",
    "SSTATS_MAX_HTTP_REQUESTS_PER_RUN": "150",
    "SSTATS_CONTEXT_MATCH_LIMIT": "320",
    "SSTATS_LOOKBACK_DAYS": "60",
    "SSTATS_RECENT_MATCHES": "12",

    # Bzzoiro: user-confirmed no practical limit.  Use hard but not infinite caps.
    "ENABLE_BZZOIRO": "true",
    "ENABLE_BZZOIRO_CONTEXT": "true",
    "BZZOIRO_ENABLED": "true",
    "BZZOIRO_PER_RUN_MAX": "1000",
    "BZZOIRO_MAX_HTTP_REQUESTS_PER_RUN": "1000",
    "BZZOIRO_CONTEXT_MATCH_LIMIT": "520",
    "BZZOIRO_MAX_PAGES": "80",

    # Weather: restore both WeatherAPI and OpenWeatherMap paths.
    "ENABLE_WEATHERAPI": "true",
    "WEATHERAPI_ENABLED": "true",
    "WEATHERAPI_PER_RUN_MAX": "12",
    "WEATHERAPI_MAX_HTTP_REQUESTS_PER_RUN": "12",
    "WEATHER_CONTEXT_ENABLED": "true",
    "WEATHER_CONTEXT_MATCH_LIMIT": "24",
    "WEATHER_CACHE_TTL_MINUTES": "240",
    "ENABLE_OPENWEATHERMAP": "true",
    "OPENWEATHERMAP_ENABLED": "true",
    "OPENWEATHERMAP_PER_RUN_MAX": "8",
    "OPENWEATHERMAP_MAX_HTTP_REQUESTS_PER_RUN": "8",
    "WEATHER_OPENWEATHERMAP_FALLBACK_ENABLED": "true",

    # SportLogic.
    "ENABLE_SPORTLOGIC": "true",
    "SPORTLOGIC_ENABLED": "true",
    "SPORTLOGIC_BASE_URL": "https://api.sportlogic.io/api/v1",
    "SPORTLOGIC_HEADER_NAME": "X-API-Key",
    "SPORTLOGIC_PER_RUN_MAX": "40",
    "SPORTLOGIC_MATCH_LIMIT": "100",
    "SPORTLOGIC_ODDS_MATCH_LIMIT": "40",

    # Additional providers: enabled with safe per-run caps.
    "ENABLE_ALLSPORTSAPI": "true",
    "ALLSPORTSAPI_ENABLED": "true",
    "ALLSPORTSAPI_PER_RUN_MAX": "4",
    "ALLSPORTSAPI_MATCH_LIMIT": "32",
    "ALLSPORTSAPI_CONTEXT_MATCH_LIMIT": "16",
    "ENABLE_FUTRIXMETRICS": "true",
    "ENABLE_FUTRIXMETRICS_CONTEXT": "true",
    "FUTRIXMETRICS_ENABLED": "true",
    "FUTRIXMETRICS_PER_RUN_MAX": "4",
    "FUTRIXMETRICS_REQUESTS_MAX_PER_RUN": "4",
    "FUTRIXMETRICS_CONTEXT_MATCH_LIMIT": "10",
    "FUTRIXMETRICS_SHORTLIST_ONLY": "false",
    "FUTRIXMETRICS_MIN_SPACING_MINUTES": "60",
    "ENABLE_GNEWS": "true",
    "ENABLE_GNEWS_CONTEXT": "true",
    "GNEWS_ENABLED": "true",
    "GNEWS_PER_RUN_MAX": "2",
    "GNEWS_MAX_HTTP_REQUESTS_PER_RUN": "2",
    "GNEWS_CONTEXT_MATCH_LIMIT": "4",
    "GNEWS_MATCH_LIMIT": "2",
    "ENABLE_NEWSAPI": "true",
    "ENABLE_NEWSAPI_CONTEXT": "true",
    "NEWSAPI_ENABLED": "true",
    "NEWSAPI_PER_RUN_MAX": "2",
    "NEWSAPI_MAX_HTTP_REQUESTS_PER_RUN": "2",
    "NEWSAPI_MATCH_LIMIT": "2",
    "NEWS_CONTEXT_MATCH_LIMIT": "6",
    "CURRENTS_NEWS_PER_RUN_MAX": "4",
    "CURRENTS_NEWS_MAX_HTTP_REQUESTS_PER_RUN": "4",
    "CURRENTS_MATCH_LIMIT": "4",
    "ENABLE_FOOTBALL_DATA": "true",
    "ENABLE_FOOTBALL_DATA_CONTEXT": "true",
    "FOOTBALL_DATA_ENABLED": "true",
    "FOOTBALL_DATA_PER_RUN_MAX": "4",
    "FOOTBALL_DATA_REQUESTS_MAX_PER_RUN": "4",
    "FOOTBALL_DATA_CONTEXT_MATCH_LIMIT": "48",
    "ENABLE_THESPORTSDB": "true",
    "ENABLE_THESPORTSDB_CONTEXT": "true",
    "THESPORTSDB_ENABLED": "true",
    "THESPORTSDB_PER_RUN_MAX": "8",
    "THESPORTSDB_REQUESTS_MAX_PER_RUN": "8",
    "THESPORTSDB_CONTEXT_MATCH_LIMIT": "72",
    "ENABLE_OPENFOOTBALL_CONTEXT": "true",
    "OPENFOOTBALL_ENABLED": "true",
    "OPENFOOTBALL_CONTEXT_MATCH_LIMIT": "120",
    "OPENFOOTBALL_MAX_HTTP_REQUESTS_PER_RUN": "8",
    "ENABLE_ODDSPAPI": "true",
    "ODDSPAPI_ENABLED": "true",
    "ODDSPAPI_PER_RUN_MAX": "1",
    "ODDSPAPI_CONTEXT_MATCH_LIMIT": "1",
    "ODDSPAPI_MATCH_LIMIT": "4",
    "ODDSPAPI_TOURNAMENT_LIMIT": "1",
    "RAPIDAPI_ODDS_FEED_PROBE_ENABLED": "true",
    "RAPIDAPI_ODDS_FEED_DAILY_LIMIT": "12",
    "RAPIDAPI_ODDS_FEED_PER_RUN_MAX": "2",
    "RAPIDAPI_DISCOVERY_ODDS_FEED_MAX_CALLS": "2",
    "RAPIDAPI_SPORTSBOOK_PROBE_ENABLED": "true",
    "RAPIDAPI_SPORTSBOOK_DAILY_LIMIT": "20",
    "RAPIDAPI_SPORTSBOOK_PER_RUN_MAX": "2",
    "RAPIDAPI_DISCOVERY_SPORTSBOOK_MAX_CALLS": "2",
    "METEOSTAT_RAPIDAPI_ENABLED": "true",
    "WEATHER_METEOSTAT_FALLBACK_ENABLED": "true",
    "RAPIDAPI_METEOSTAT_PER_RUN_MAX": "2",
    "METEOSTAT_MAX_HTTP_REQUESTS_PER_RUN": "2",
    "RAPIDAPI_FREE_FOOTBALL_PROBE_ENABLED": "true",
    "RAPIDAPI_FREE_FOOTBALL_DAILY_LIMIT": "3",
    "RAPIDAPI_FREE_FOOTBALL_PER_RUN_MAX": "1",
    "RAPIDAPI_DISCOVERY_FREE_FOOTBALL_MAX_CALLS": "1",
    "RAPIDAPI_SPORTAPI7_PROBE_ENABLED": "true",
    "RAPIDAPI_SPORTAPI7_DAILY_LIMIT": "24",
    "RAPIDAPI_SPORTAPI7_PER_RUN_MAX": "2",
    "RAPIDAPI_DISCOVERY_SPORTAPI7_MAX_CALLS": "2",
}


BUDGET_OVERRIDES: dict[str, dict[str, Any]] = {
    "sstats": {
        "enabled": True,
        "per_run_max": 150,
        "safe_daily_budget": 1800,
        "min_spacing_minutes": 0,
        "limit": {"requests_per_minute": 150, "safe_runs_per_day_assumption": 12},
        "env": {
            "ENABLE_SSTATS": "true",
            "ENABLE_SSTATS_CONTEXT": "true",
            "SSTATS_ENABLED": "true",
            "SSTATS_PER_RUN_MAX": "150",
            "SSTATS_REQUESTS_MAX_PER_RUN": "150",
            "SSTATS_MAX_HTTP_REQUESTS_PER_RUN": "150",
            "SSTATS_CONTEXT_MATCH_LIMIT": "320",
            "SSTATS_LOOKBACK_DAYS": "60",
            "SSTATS_RECENT_MATCHES": "12",
        },
    },
    "bzzoiro": {
        "enabled": True,
        "per_run_max": 1000,
        "safe_daily_budget": 200000,
        "min_spacing_minutes": 0,
        "limit": {"free_forever_no_rate_limit": True},
        "env": {
            "ENABLE_BZZOIRO": "true",
            "ENABLE_BZZOIRO_CONTEXT": "true",
            "BZZOIRO_ENABLED": "true",
            "BZZOIRO_PER_RUN_MAX": "1000",
            "BZZOIRO_MAX_HTTP_REQUESTS_PER_RUN": "1000",
            "BZZOIRO_CONTEXT_MATCH_LIMIT": "520",
            "BZZOIRO_MAX_PAGES": "80",
        },
    },
    "weatherapi": {
        "enabled": True,
        "per_run_max": 12,
        "safe_daily_budget": 288,
        "safe_monthly_budget": 9000,
        "min_spacing_minutes": 0,
        "env": {
            "ENABLE_WEATHERAPI": "true",
            "WEATHERAPI_ENABLED": "true",
            "WEATHERAPI_PER_RUN_MAX": "12",
            "WEATHERAPI_MAX_HTTP_REQUESTS_PER_RUN": "12",
            "WEATHER_CONTEXT_ENABLED": "true",
            "WEATHER_CONTEXT_MATCH_LIMIT": "24",
            "WEATHER_CACHE_TTL_MINUTES": "240",
        },
    },
    "openweathermap": {
        "enabled": True,
        "per_run_max": 8,
        "safe_daily_budget": 240,
        "min_spacing_minutes": 0,
        "env": {
            "ENABLE_OPENWEATHERMAP": "true",
            "OPENWEATHERMAP_ENABLED": "true",
            "OPENWEATHERMAP_PER_RUN_MAX": "8",
            "OPENWEATHERMAP_MAX_HTTP_REQUESTS_PER_RUN": "8",
            "WEATHER_OPENWEATHERMAP_FALLBACK_ENABLED": "true",
        },
    },
    "allsportsapi": {"enabled": True, "per_run_max": 4, "safe_daily_budget": 48, "min_spacing_minutes": 60},
    "futrixmetrics": {"enabled": True, "per_run_max": 4, "safe_daily_budget": 60, "safe_monthly_budget": 1500, "min_spacing_minutes": 60},
    "gnews": {"enabled": True, "per_run_max": 2, "safe_daily_budget": 36, "min_spacing_minutes": 60},
    "newsapi_currents": {"enabled": True, "per_run_max": 4, "safe_daily_budget": 72, "min_spacing_minutes": 60},
    "football_data": {"enabled": True, "per_run_max": 4, "safe_daily_budget": 72, "min_spacing_minutes": 1},
    "thesportsdb": {"enabled": True, "per_run_max": 8, "safe_daily_budget": 144, "min_spacing_minutes": 1},
    "openfootball_public": {"enabled": True, "per_run_max": 8, "safe_daily_budget": 192, "min_spacing_minutes": 0},
    "sportlogic": {"enabled": True, "per_run_max": 40, "safe_daily_budget": 480, "min_spacing_minutes": 0},
    "oddsfeed": {"enabled": True, "per_run_max": 2, "safe_daily_budget": 12, "safe_monthly_budget": 240, "min_spacing_minutes": 120},
    "sportsbook_api": {"enabled": True, "per_run_max": 2, "safe_daily_budget": 20, "min_spacing_minutes": 120},
    "meteostat": {"enabled": True, "per_run_max": 2, "safe_daily_budget": 12, "safe_monthly_budget": 240, "min_spacing_minutes": 120},
    "sportapi": {"enabled": True, "per_run_max": 2, "safe_daily_budget": 24, "min_spacing_minutes": 120},
    "freeapilivefootball": {"enabled": True, "per_run_max": 1, "safe_daily_budget": 3, "safe_monthly_budget": 90, "min_spacing_minutes": 240},
}


TEXT_REPLACEMENTS = {
    "'sstats': 120,": "'sstats': 150,",
    "'bzzoiro': 500,": "'bzzoiro': 1000,",
    "'per_run_max': 120,\n        'safe_daily_budget': 50000,": "'per_run_max': 150,\n        'safe_daily_budget': 1800,",
    "'SSTATS_PER_RUN_MAX': '120'": "'SSTATS_PER_RUN_MAX': '150'",
    "'SSTATS_REQUESTS_MAX_PER_RUN': '120'": "'SSTATS_REQUESTS_MAX_PER_RUN': '150'",
    "'SSTATS_MAX_HTTP_REQUESTS_PER_RUN': '120'": "'SSTATS_MAX_HTTP_REQUESTS_PER_RUN': '150'",
    "'SSTATS_CONTEXT_MATCH_LIMIT': '260'": "'SSTATS_CONTEXT_MATCH_LIMIT': '320'",
    "'SSTATS_LOOKBACK_DAYS': '45'": "'SSTATS_LOOKBACK_DAYS': '60'",
    "'SSTATS_RECENT_MATCHES': '10'": "'SSTATS_RECENT_MATCHES': '12'",
    "'per_run_max': 500,\n        'safe_daily_budget': 200000,": "'per_run_max': 1000,\n        'safe_daily_budget': 200000,",
    "'BZZOIRO_PER_RUN_MAX': '500'": "'BZZOIRO_PER_RUN_MAX': '1000'",
    "'BZZOIRO_MAX_HTTP_REQUESTS_PER_RUN': '500'": "'BZZOIRO_MAX_HTTP_REQUESTS_PER_RUN': '1000'",
    "'BZZOIRO_MAX_PAGES': '24'": "'BZZOIRO_MAX_PAGES': '80'",
}


def _apply_env_aliases_and_overrides() -> None:
    # Support common alternate secret names after user rotated weather keys.
    _alias_env("WEATHERAPI_KEY", "WEATHER_API_KEY", "WEATHERAPI_API_KEY", "WEATHERAPI_TOKEN")
    _alias_env("WEATHER_API_KEY", "WEATHERAPI_KEY", "WEATHERAPI_API_KEY", "WEATHERAPI_TOKEN")
    _alias_env("WEATHERAPI_API_KEY", "WEATHERAPI_KEY", "WEATHER_API_KEY", "WEATHERAPI_TOKEN")
    _alias_env("OPENWEATHERMAP_API_KEY", "OPENWEATHER_API_KEY", "OPENWEATHERMAP_KEY", "OPENWEATHER_KEY", "OWM_API_KEY")
    _alias_env("OPENWEATHER_API_KEY", "OPENWEATHERMAP_API_KEY", "OPENWEATHERMAP_KEY", "OPENWEATHER_KEY", "OWM_API_KEY")
    _alias_env("OPENWEATHERMAP_KEY", "OPENWEATHERMAP_API_KEY", "OPENWEATHER_API_KEY", "OPENWEATHER_KEY", "OWM_API_KEY")
    for key, value in AGGRESSIVE_ENV.items():
        _setenv(key, value)


def _append_github_env() -> None:
    env_path = os.getenv("GITHUB_ENV")
    if not env_path:
        return
    payload = dict(AGGRESSIVE_ENV)
    for key in ("WEATHERAPI_KEY", "WEATHER_API_KEY", "WEATHERAPI_API_KEY", "OPENWEATHERMAP_API_KEY", "OPENWEATHER_API_KEY", "OPENWEATHERMAP_KEY"):
        value = os.getenv(key)
        if value:
            payload[key] = value
    try:
        with open(env_path, "a", encoding="utf-8") as fh:
            for key in sorted(payload):
                fh.write(f"{key}={payload[key]}\n")
    except Exception:
        return


def _patch_budget_json() -> None:
    path = ROOT / "config" / "provider_request_budget.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        providers = payload.setdefault("providers", {})
        for name, override in BUDGET_OVERRIDES.items():
            current = providers.get(name)
            if isinstance(current, dict):
                providers[name] = _merge_dict(current, override)
            else:
                providers[name] = dict(override)
        payload["version"] = "v16-max-api-usage-safe-quotas"
        notes = payload.setdefault("notes", [])
        if isinstance(notes, list):
            notes.append("SStats raised to 150 req/run based on user-confirmed 150/min quota; Bzzoiro raised to 1000 req/run; weather aliases restored.")
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except Exception:
        return


def _patch_provider_budget_py(text: str) -> str:
    for old, new in TEXT_REPLACEMENTS.items():
        text = text.replace(old, new)
    # Keep recovery only for high-value/high-quota sources.  This prevents small providers from burning stale daily caps.
    text = text.replace(
        "and name in HARIZON_CRITICAL_PROVIDERS\n            and decision['reason'].startswith('daily_budget_exhausted:')",
        "and name in {'odds_api_io', 'bzzoiro', 'sstats', 'sportlogic'}\n            and decision['reason'].startswith('daily_budget_exhausted:')",
    )
    return text


def _patch_runtime_policy_py(text: str) -> str:
    if '"SSTATS_PER_RUN_MAX": policy_value("SSTATS_PER_RUN_MAX", "150")' not in text:
        insert_after = '        "BZZOIRO_MAX_PAGES": policy_value("BZZOIRO_MAX_PAGES", "24"),\n'
        insert = (
            '        "BZZOIRO_MAX_PAGES": policy_value("BZZOIRO_MAX_PAGES", "80"),\n'
            '        "BZZOIRO_PER_RUN_MAX": policy_value("BZZOIRO_PER_RUN_MAX", "1000"),\n'
            '        "BZZOIRO_MAX_HTTP_REQUESTS_PER_RUN": policy_value("BZZOIRO_MAX_HTTP_REQUESTS_PER_RUN", "1000"),\n'
            '        "BZZOIRO_CONTEXT_MATCH_LIMIT": policy_value("BZZOIRO_CONTEXT_MATCH_LIMIT", "520"),\n'
            '        "SSTATS_PER_RUN_MAX": policy_value("SSTATS_PER_RUN_MAX", "150"),\n'
            '        "SSTATS_REQUESTS_MAX_PER_RUN": policy_value("SSTATS_REQUESTS_MAX_PER_RUN", "150"),\n'
            '        "SSTATS_MAX_HTTP_REQUESTS_PER_RUN": policy_value("SSTATS_MAX_HTTP_REQUESTS_PER_RUN", "150"),\n'
            '        "SSTATS_CONTEXT_MATCH_LIMIT": policy_value("SSTATS_CONTEXT_MATCH_LIMIT", "320"),\n'
            '        "SSTATS_LOOKBACK_DAYS": policy_value("SSTATS_LOOKBACK_DAYS", "60"),\n'
            '        "SSTATS_RECENT_MATCHES": policy_value("SSTATS_RECENT_MATCHES", "12"),\n'
            '        "ENABLE_WEATHERAPI": "true",\n'
            '        "WEATHERAPI_ENABLED": "true",\n'
            '        "WEATHERAPI_PER_RUN_MAX": policy_value("WEATHERAPI_PER_RUN_MAX", "12"),\n'
            '        "WEATHERAPI_MAX_HTTP_REQUESTS_PER_RUN": policy_value("WEATHERAPI_MAX_HTTP_REQUESTS_PER_RUN", "12"),\n'
            '        "WEATHER_CONTEXT_ENABLED": "true",\n'
            '        "WEATHER_CONTEXT_MATCH_LIMIT": policy_value("WEATHER_CONTEXT_MATCH_LIMIT", "24"),\n'
            '        "ENABLE_OPENWEATHERMAP": "true",\n'
            '        "OPENWEATHERMAP_ENABLED": "true",\n'
            '        "OPENWEATHERMAP_PER_RUN_MAX": policy_value("OPENWEATHERMAP_PER_RUN_MAX", "8"),\n'
            '        "OPENWEATHERMAP_MAX_HTTP_REQUESTS_PER_RUN": policy_value("OPENWEATHERMAP_MAX_HTTP_REQUESTS_PER_RUN", "8"),\n'
        )
        if insert_after in text:
            text = text.replace(insert_after, insert, 1)
    text = text.replace('"BZZOIRO_MAX_PAGES": policy_value("BZZOIRO_MAX_PAGES", "24")', '"BZZOIRO_MAX_PAGES": policy_value("BZZOIRO_MAX_PAGES", "80")')
    return text


def apply_api_max_usage_patch() -> None:
    _apply_env_aliases_and_overrides()
    _append_github_env()
    _patch_budget_json()
    _patch_text_file("scripts/apply_provider_request_budget.py", _patch_provider_budget_py)
    _patch_text_file("scripts/apply_harizon_runtime_policy.py", _patch_runtime_policy_py)


try:
    apply_api_max_usage_patch()
except Exception:
    pass
