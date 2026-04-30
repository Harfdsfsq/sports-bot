from __future__ import annotations

"""Per-run-only API limits for Harizon.

User policy: every provider is limited only by the current run. Daily/monthly
request budgets and accumulated usage counters are disabled.
"""

import json
import os
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "config" / "provider_request_budget.json"
STATE_PATH = ROOT / ".data" / "provider_request_budget_state.json"

PER_RUN_LIMITS: dict[str, int] = {
    "odds_api_io": 200,
    "bzzoiro": 1000,
    "sstats": 150,
    "sportlogic": 40,
    "espn_public": 18,
    "football_data": 4,
    "thesportsdb": 8,
    "openfootball_public": 8,
    "newsapi_currents": 4,
    "gnews": 2,
    "allsportsapi": 4,
    "oddspapi": 1,
    "futrixmetrics": 4,
    "weatherapi": 24,
    "openweathermap": 12,
    "sportsbook_api": 2,
    "meteostat": 2,
    "oddsfeed": 2,
    "freeapilivefootball": 1,
    "sportapi": 2,
}

ENV_OVERRIDES: dict[str, str] = {
    "ALL_SOURCES_FREE_MAXIMIZE": "true",
    "PROVIDER_REQUEST_BUDGET_MODE": "per_run_only",
    "PROVIDER_REQUEST_BUDGET_DISABLE_DAILY_MONTHLY": "true",

    "ODDS_API_IO_ACCOUNT1_PER_RUN_MAX": "100",
    "ODDS_API_IO_ACCOUNT2_PER_RUN_MAX": "100",
    "ODDS_API_IO_PER_RUN_MAX": "200",
    "ODDS_API_IO_MAX_HTTP_REQUESTS_PER_RUN": "200",
    "ODDS_API_IO_PAGE_LIMIT": "100",
    "ODDS_API_IO_MAX_EVENT_PAGES_PER_SPORT": "36",
    "MAX_MATCHES_FOR_ODDS_FETCH": "520",

    "ENABLE_SSTATS": "true",
    "ENABLE_SSTATS_CONTEXT": "true",
    "SSTATS_ENABLED": "true",
    "SSTATS_PER_RUN_MAX": "150",
    "SSTATS_REQUESTS_MAX_PER_RUN": "150",
    "SSTATS_MAX_HTTP_REQUESTS_PER_RUN": "150",
    "SSTATS_CONTEXT_MATCH_LIMIT": "320",
    "SSTATS_LOOKBACK_DAYS": "60",
    "SSTATS_RECENT_MATCHES": "12",

    "ENABLE_BZZOIRO": "true",
    "ENABLE_BZZOIRO_CONTEXT": "true",
    "BZZOIRO_ENABLED": "true",
    "BZZOIRO_PER_RUN_MAX": "1000",
    "BZZOIRO_MAX_HTTP_REQUESTS_PER_RUN": "1000",
    "BZZOIRO_CONTEXT_MATCH_LIMIT": "520",
    "BZZOIRO_MAX_PAGES": "80",

    "ENABLE_SPORTLOGIC": "true",
    "SPORTLOGIC_ENABLED": "true",
    "SPORTLOGIC_BASE_URL": "https://api.sportlogic.io/api/v1",
    "SPORTLOGIC_HEADER_NAME": "X-API-Key",
    "SPORTLOGIC_PER_RUN_MAX": "40",
    "SPORTLOGIC_MATCH_LIMIT": "120",
    "SPORTLOGIC_ODDS_MATCH_LIMIT": "40",

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
    "FUTRIXMETRICS_MIN_SPACING_MINUTES": "0",
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
    "RAPIDAPI_ODDS_FEED_PER_RUN_MAX": "2",
    "RAPIDAPI_DISCOVERY_ODDS_FEED_MAX_CALLS": "2",
    "RAPIDAPI_SPORTSBOOK_PROBE_ENABLED": "true",
    "RAPIDAPI_SPORTSBOOK_PER_RUN_MAX": "2",
    "RAPIDAPI_DISCOVERY_SPORTSBOOK_MAX_CALLS": "2",
    "METEOSTAT_RAPIDAPI_ENABLED": "true",
    "WEATHER_METEOSTAT_FALLBACK_ENABLED": "true",
    "RAPIDAPI_METEOSTAT_PER_RUN_MAX": "2",
    "METEOSTAT_MAX_HTTP_REQUESTS_PER_RUN": "2",
    "RAPIDAPI_FREE_FOOTBALL_PROBE_ENABLED": "true",
    "RAPIDAPI_FREE_FOOTBALL_PER_RUN_MAX": "1",
    "RAPIDAPI_SPORTAPI7_PROBE_ENABLED": "true",
    "RAPIDAPI_SPORTAPI7_PER_RUN_MAX": "2",
}

SECRET_ALIASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("WEATHERAPI_KEY", ("WEATHER_API_KEY", "WEATHERAPI_API_KEY", "WEATHERAPI_TOKEN")),
    ("WEATHER_API_KEY", ("WEATHERAPI_KEY", "WEATHERAPI_API_KEY", "WEATHERAPI_TOKEN")),
    ("WEATHERAPI_API_KEY", ("WEATHERAPI_KEY", "WEATHER_API_KEY", "WEATHERAPI_TOKEN")),
    ("OPENWEATHERMAP_API_KEY", ("OPENWEATHER_API_KEY", "OPENWEATHERMAP_KEY", "OPENWEATHER_KEY", "OWM_API_KEY")),
    ("OPENWEATHER_API_KEY", ("OPENWEATHERMAP_API_KEY", "OPENWEATHERMAP_KEY", "OPENWEATHER_KEY", "OWM_API_KEY")),
    ("OPENWEATHERMAP_KEY", ("OPENWEATHERMAP_API_KEY", "OPENWEATHER_API_KEY", "OPENWEATHER_KEY", "OWM_API_KEY")),
)


def _alias_env(target: str, aliases: tuple[str, ...]) -> None:
    if os.getenv(target):
        return
    for alias in aliases:
        value = os.getenv(alias)
        if value:
            os.environ[target] = value
            return


def _append_github_env(values: dict[str, str]) -> None:
    env_path = os.getenv("GITHUB_ENV")
    if not env_path:
        return
    try:
        with open(env_path, "a", encoding="utf-8") as fh:
            for key in sorted(values):
                fh.write(f"{key}={values[key]}\n")
    except Exception:
        pass


def _strip_period_budgets(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _strip_period_budgets(v) for k, v in obj.items() if k not in {"safe_daily_budget", "safe_monthly_budget", "daily_budget", "monthly_budget"}}
    if isinstance(obj, list):
        return [_strip_period_budgets(v) for v in obj]
    return obj


def _patch_policy_json() -> None:
    try:
        payload = json.loads(POLICY_PATH.read_text(encoding="utf-8")) if POLICY_PATH.exists() else {}
        payload = _strip_period_budgets(payload)
        payload["version"] = "v18-per-run-only-api-limits"
        payload["description"] = "All active providers are limited only by per-run caps. Daily and monthly request budgets are disabled."
        payload["period_budgets_disabled"] = True
        providers = payload.setdefault("providers", {})
        for name, limit in PER_RUN_LIMITS.items():
            row = providers.setdefault(name, {})
            if not isinstance(row, dict):
                row = {}
                providers[name] = row
            row["enabled"] = True
            row["per_run_max"] = int(limit)
            row["min_spacing_minutes"] = 0
            row.pop("allowed_msk_hours", None)
            row.pop("manual_per_run_max", None)
        notes = payload.setdefault("notes", [])
        if isinstance(notes, list):
            notes.append("Daily/monthly budgets removed by user request; only per-run limits apply.")
        POLICY_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except Exception:
        pass


def _patch_budget_state() -> None:
    try:
        state = json.loads(STATE_PATH.read_text(encoding="utf-8")) if STATE_PATH.exists() else {}
        providers = state.get("providers") if isinstance(state.get("providers"), dict) else {}
        for row in providers.values():
            if isinstance(row, dict):
                row["daily"] = {}
                row["monthly"] = {}
                row.pop("cooldown_until", None)
                row.pop("cooldown_reason", None)
        state["period_budgets_disabled"] = True
        state["budget_mode"] = "per_run_only"
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except Exception:
        pass


def _patch_text_file(rel_path: str, patcher) -> None:
    path = ROOT / rel_path
    try:
        old = path.read_text(encoding="utf-8")
        new = patcher(old)
        if new != old:
            path.write_text(new, encoding="utf-8")
    except Exception:
        pass


def _patch_provider_budget_py(text: str) -> str:
    if "PER_RUN_ONLY_LIMITS = True" not in text:
        text = text.replace("ALL_HOURS = list(range(24))\n", "ALL_HOURS = list(range(24))\nPER_RUN_ONLY_LIMITS = True\n", 1)
    text = text.replace("daily_budget = as_int(cfg.get('safe_daily_budget'), 0)", "daily_budget = 0 if PER_RUN_ONLY_LIMITS else as_int(cfg.get('safe_daily_budget'), 0)")
    text = text.replace("monthly_budget = as_int(cfg.get('safe_monthly_budget'), 0)", "monthly_budget = 0 if PER_RUN_ONLY_LIMITS else as_int(cfg.get('safe_monthly_budget'), 0)")
    text = text.replace("'daily_budget': as_int(cfg.get('safe_daily_budget'), 0),", "'daily_budget': None,")
    text = text.replace("'monthly_budget': as_int(cfg.get('safe_monthly_budget'), 0),", "'monthly_budget': None,")
    text = text.replace("'daily_used_before': usage(row, 'daily', dkey),", "'daily_used_before': None,")
    text = text.replace("'monthly_used_before': usage(row, 'monthly', mkey),", "'monthly_used_before': None,")
    text = text.replace("add_usage(row, 'daily', dkey, grant)\n    add_usage(row, 'monthly', mkey, grant)", "# daily/monthly accounting disabled: per-run limits only")
    text = text.replace("add_usage(row, 'daily', dkey, grant)\n            add_usage(row, 'monthly', mkey, grant)", "# daily/monthly accounting disabled: per-run limits only")
    text = text.replace("and decision['reason'].startswith('daily_budget_exhausted:')", "and False  # period budgets disabled")
    text = text.replace("'Free-source maximize mode is active and raises budgets/context caps for free providers from RULES.txt quotas.'", "'Provider budgets are per-run only; daily/monthly caps are disabled by user request.'")
    text = text.replace("'Providers with explicit monthly cooldowns, such as OddsPapi after REQUEST_LIMIT_EXCEEDED, remain cooldown-skipped until reset.'", "'Cooldowns from fatal/auth provider errors may still apply, but daily/monthly request caps do not.'")
    return text


def _apply_env() -> None:
    for target, aliases in SECRET_ALIASES:
        _alias_env(target, aliases)
    os.environ.update(ENV_OVERRIDES)
    secret_payload = dict(ENV_OVERRIDES)
    for target, _ in SECRET_ALIASES:
        value = os.getenv(target)
        if value:
            secret_payload[target] = value
    _append_github_env(secret_payload)


def apply_api_max_usage_patch() -> None:
    _apply_env()
    _patch_policy_json()
    _patch_budget_state()
    _patch_text_file("scripts/apply_provider_request_budget.py", _patch_provider_budget_py)


try:
    apply_api_max_usage_patch()
except Exception:
    pass
