from __future__ import annotations

"""Aggressive but quota-safe API usage patch.

This module is imported from both root `sitecustomize.py` and
`scripts/sitecustomize.py`.  It must be safe to run many times.
"""

import hashlib
import json
import os
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
STATE_PATH = ROOT / ".data" / "provider_request_budget_state.json"
POLICY_PATH = ROOT / "config" / "provider_request_budget.json"


AGGRESSIVE_ENV: dict[str, str] = {
    "ALL_SOURCES_FREE_MAXIMIZE": "true",

    # SStats: user-confirmed limit is 150 requests/minute.  Cap one run at 150.
    "ENABLE_SSTATS": "true",
    "ENABLE_SSTATS_CONTEXT": "true",
    "SSTATS_ENABLED": "true",
    "SSTATS_PER_RUN_MAX": "150",
    "SSTATS_REQUESTS_MAX_PER_RUN": "150",
    "SSTATS_MAX_HTTP_REQUESTS_PER_RUN": "150",
    "SSTATS_CONTEXT_MATCH_LIMIT": "320",
    "SSTATS_LOOKBACK_DAYS": "60",
    "SSTATS_RECENT_MATCHES": "12",

    # Bzzoiro: user-confirmed no practical limit.  Use strongly, not infinitely.
    "ENABLE_BZZOIRO": "true",
    "ENABLE_BZZOIRO_CONTEXT": "true",
    "BZZOIRO_ENABLED": "true",
    "BZZOIRO_PER_RUN_MAX": "1000",
    "BZZOIRO_MAX_HTTP_REQUESTS_PER_RUN": "1000",
    "BZZOIRO_CONTEXT_MATCH_LIMIT": "520",
    "BZZOIRO_MAX_PAGES": "80",

    # Weather restored.  Key rotation reset is handled below.
    "ENABLE_WEATHERAPI": "true",
    "WEATHERAPI_ENABLED": "true",
    "WEATHERAPI_PER_RUN_MAX": "24",
    "WEATHERAPI_MAX_HTTP_REQUESTS_PER_RUN": "24",
    "WEATHER_CONTEXT_ENABLED": "true",
    "WEATHER_CONTEXT_MATCH_LIMIT": "48",
    "WEATHER_CACHE_TTL_MINUTES": "240",
    "WEATHER_ALLOW_TEAM_NAME_FALLBACK": "true",
    "WEATHER_SHORTLIST_ONLY": "false",
    "ENABLE_OPENWEATHERMAP": "true",
    "OPENWEATHERMAP_ENABLED": "true",
    "OPENWEATHERMAP_PER_RUN_MAX": "12",
    "OPENWEATHERMAP_MAX_HTTP_REQUESTS_PER_RUN": "12",
    "WEATHER_OPENWEATHERMAP_FALLBACK_ENABLED": "true",

    # SportLogic.
    "ENABLE_SPORTLOGIC": "true",
    "SPORTLOGIC_ENABLED": "true",
    "SPORTLOGIC_BASE_URL": "https://api.sportlogic.io/api/v1",
    "SPORTLOGIC_HEADER_NAME": "X-API-Key",
    "SPORTLOGIC_PER_RUN_MAX": "40",
    "SPORTLOGIC_MATCH_LIMIT": "120",
    "SPORTLOGIC_ODDS_MATCH_LIMIT": "40",

    # Extra providers, enabled with bounded grants.
    "ENABLE_ALLSPORTSAPI": "true",
    "ALLSPORTSAPI_ENABLED": "true",
    "ALLSPORTSAPI_PER_RUN_MAX": "4",
    "ALLSPORTSAPI_MATCH_LIMIT": "40",
    "ALLSPORTSAPI_CONTEXT_MATCH_LIMIT": "20",
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
    "RAPIDAPI_SPORTAPI7_PROBE_ENABLED": "true",
    "RAPIDAPI_SPORTAPI7_DAILY_LIMIT": "24",
    "RAPIDAPI_SPORTAPI7_PER_RUN_MAX": "2",
}

BUDGET_OVERRIDES: dict[str, dict[str, Any]] = {
    "sstats": {
        "enabled": True,
        "per_run_max": 150,
        "safe_daily_budget": 1800,
        "min_spacing_minutes": 0,
        "limit": {"requests_per_minute": 150, "safe_runs_per_day_assumption": 12},
        "env": {k: v for k, v in AGGRESSIVE_ENV.items() if k.startswith("SSTATS") or k.startswith("ENABLE_SSTATS")},
    },
    "bzzoiro": {
        "enabled": True,
        "per_run_max": 1000,
        "safe_daily_budget": 200000,
        "min_spacing_minutes": 0,
        "limit": {"free_forever_no_rate_limit": True},
        "env": {k: v for k, v in AGGRESSIVE_ENV.items() if k.startswith("BZZOIRO") or k.startswith("ENABLE_BZZOIRO")},
    },
    "weatherapi": {
        "enabled": True,
        "per_run_max": 24,
        "safe_daily_budget": 576,
        "safe_monthly_budget": 9000,
        "min_spacing_minutes": 0,
        "secret_env_keys": ["WEATHERAPI_KEY", "WEATHER_API_KEY", "WEATHERAPI_API_KEY", "WEATHERAPI_TOKEN"],
        "env": {k: v for k, v in AGGRESSIVE_ENV.items() if k.startswith("WEATHER") or k.startswith("ENABLE_WEATHERAPI")},
    },
    "openweathermap": {
        "enabled": True,
        "per_run_max": 12,
        "safe_daily_budget": 360,
        "min_spacing_minutes": 0,
        "secret_env_keys": ["OPENWEATHERMAP_API_KEY", "OPENWEATHER_API_KEY", "OPENWEATHERMAP_KEY", "OPENWEATHER_KEY", "OWM_API_KEY"],
        "env": {k: v for k, v in AGGRESSIVE_ENV.items() if k.startswith("OPENWEATHER") or k.startswith("ENABLE_OPENWEATHERMAP") or k == "WEATHER_OPENWEATHERMAP_FALLBACK_ENABLED"},
    },
    "sportlogic": {"enabled": True, "per_run_max": 40, "safe_daily_budget": 480, "min_spacing_minutes": 0},
    "allsportsapi": {"enabled": True, "per_run_max": 4, "safe_daily_budget": 48, "min_spacing_minutes": 60},
    "futrixmetrics": {"enabled": True, "per_run_max": 4, "safe_daily_budget": 60, "safe_monthly_budget": 1500, "min_spacing_minutes": 60},
    "gnews": {"enabled": True, "per_run_max": 2, "safe_daily_budget": 36, "min_spacing_minutes": 60},
    "newsapi_currents": {"enabled": True, "per_run_max": 4, "safe_daily_budget": 72, "min_spacing_minutes": 60},
    "football_data": {"enabled": True, "per_run_max": 4, "safe_daily_budget": 72, "min_spacing_minutes": 1},
    "thesportsdb": {"enabled": True, "per_run_max": 8, "safe_daily_budget": 144, "min_spacing_minutes": 1},
    "openfootball_public": {"enabled": True, "per_run_max": 8, "safe_daily_budget": 192, "min_spacing_minutes": 0},
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


def _merge_dict(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge_dict(dict(result[key]), value)
        else:
            result[key] = value
    return result


def _alias_env(target: str, *aliases: str) -> None:
    if os.getenv(target):
        return
    for alias in aliases:
        value = os.getenv(alias)
        if value:
            os.environ[target] = value
            return


def _provider_key_fingerprint(*keys: str) -> str | None:
    values = [os.getenv(key, "").strip() for key in keys if os.getenv(key, "").strip()]
    if not values:
        return None
    raw = "|".join(values).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


def _reset_provider_state_on_key_change(provider: str, fingerprint: str | None) -> None:
    if not fingerprint:
        return
    try:
        state = json.loads(STATE_PATH.read_text(encoding="utf-8")) if STATE_PATH.exists() else {}
        providers = state.setdefault("providers", {})
        row = providers.setdefault(provider, {})
        old = row.get("key_fingerprint")
        if old != fingerprint:
            row["daily"] = {}
            row["monthly"] = {}
            row["cooldown_until"] = None
            row["cooldown_reason"] = None
            row["key_fingerprint"] = fingerprint
            row["budget_reset_reason"] = "api_key_changed_or_first_seen"
            STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
            STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except Exception:
        return


def _apply_env() -> None:
    _alias_env("WEATHERAPI_KEY", "WEATHER_API_KEY", "WEATHERAPI_API_KEY", "WEATHERAPI_TOKEN")
    _alias_env("WEATHER_API_KEY", "WEATHERAPI_KEY", "WEATHERAPI_API_KEY", "WEATHERAPI_TOKEN")
    _alias_env("WEATHERAPI_API_KEY", "WEATHERAPI_KEY", "WEATHER_API_KEY", "WEATHERAPI_TOKEN")
    _alias_env("OPENWEATHERMAP_API_KEY", "OPENWEATHER_API_KEY", "OPENWEATHERMAP_KEY", "OPENWEATHER_KEY", "OWM_API_KEY")
    _alias_env("OPENWEATHER_API_KEY", "OPENWEATHERMAP_API_KEY", "OPENWEATHERMAP_KEY", "OPENWEATHER_KEY", "OWM_API_KEY")
    _alias_env("OPENWEATHERMAP_KEY", "OPENWEATHERMAP_API_KEY", "OPENWEATHER_API_KEY", "OPENWEATHER_KEY", "OWM_API_KEY")
    for key, value in AGGRESSIVE_ENV.items():
        os.environ[key] = str(value)
    _reset_provider_state_on_key_change("weatherapi", _provider_key_fingerprint("WEATHERAPI_KEY", "WEATHER_API_KEY", "WEATHERAPI_API_KEY", "WEATHERAPI_TOKEN"))
    _reset_provider_state_on_key_change("openweathermap", _provider_key_fingerprint("OPENWEATHERMAP_API_KEY", "OPENWEATHER_API_KEY", "OPENWEATHERMAP_KEY", "OPENWEATHER_KEY", "OWM_API_KEY"))


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
    try:
        payload = json.loads(POLICY_PATH.read_text(encoding="utf-8")) if POLICY_PATH.exists() else {}
        providers = payload.setdefault("providers", {})
        for name, override in BUDGET_OVERRIDES.items():
            current = providers.get(name)
            providers[name] = _merge_dict(current if isinstance(current, dict) else {}, override)
        payload["version"] = "v17-max-api-usage-key-rotation-reset"
        notes = payload.setdefault("notes", [])
        if isinstance(notes, list) and not any("key-rotation" in str(x) for x in notes):
            notes.append("SStats=150/run, Bzzoiro=1000/run, weather key-rotation resets stale daily budget state.")
        POLICY_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except Exception:
        return


def _patch_text_file(rel_path: str, patcher) -> None:
    path = ROOT / rel_path
    try:
        old = path.read_text(encoding="utf-8")
        new = patcher(old)
        if new != old:
            path.write_text(new, encoding="utf-8")
    except Exception:
        return


def _patch_provider_budget_py(text: str) -> str:
    for old, new in TEXT_REPLACEMENTS.items():
        text = text.replace(old, new)
    text = text.replace(
        "and name in HARIZON_CRITICAL_PROVIDERS\n            and decision['reason'].startswith('daily_budget_exhausted:')",
        "and name in {'odds_api_io', 'bzzoiro', 'sstats', 'sportlogic'}\n            and decision['reason'].startswith('daily_budget_exhausted:')",
    )
    return text


def _patch_runtime_policy_py(text: str) -> str:
    text = text.replace('"BZZOIRO_MAX_PAGES": policy_value("BZZOIRO_MAX_PAGES", "24")', '"BZZOIRO_MAX_PAGES": policy_value("BZZOIRO_MAX_PAGES", "80")')
    text = text.replace('"WEATHERAPI_PER_RUN_MAX": "80"', '"WEATHERAPI_PER_RUN_MAX": policy_value("WEATHERAPI_PER_RUN_MAX", "24")')
    return text


def apply_api_max_usage_patch() -> None:
    _apply_env()
    _append_github_env()
    _patch_budget_json()
    _patch_text_file("scripts/apply_provider_request_budget.py", _patch_provider_budget_py)
    _patch_text_file("scripts/apply_harizon_runtime_policy.py", _patch_runtime_policy_py)


try:
    apply_api_max_usage_patch()
except Exception:
    pass
