from __future__ import annotations

"""Runtime provider budget guard.

Sets conservative defaults for the normal bot run so free-tier APIs are used as
context/cache/fallback inputs without consuming too much quota or being counted
as price confirmation.
"""

import json
import os
from pathlib import Path
from typing import Any

PATCH_MARKER = "_harizon_runtime_provider_budget_guard_v1"
POLICY_PATH = Path("config/provider_free_quota_policy.json")

ENV_DEFAULTS = {
    # Generic runtime switches.
    "PROVIDER_FREE_QUOTA_POLICY_ENABLED": "true",
    "PROVIDER_CONTEXT_SOURCES_DO_NOT_CONFIRM_PRICE": "true",
    "RUNTIME_OPTIONAL_PROVIDERS_ENABLED": "true",
    "RUNTIME_PROVIDER_DIAGNOSTICS_ENABLED": "true",
    # Hard odds/price safety.
    "MATCH_TOTAL_OVER15_MAX_REASONABLE_ODDS": "1.65",
    "MATCH_TOTAL_OVER15_MIN_EXACT_BOOKS": "3",
    "MARKET_INTEGRITY_USE_EXACT_PRICE_SOURCES": "true",
    # Core odds.
    "ODDS_API_IO_MAX_REQUESTS_PER_RUN": "140",
    "ODDS_API_IO_ACCOUNT2_MAX_REQUESTS_PER_RUN": "140",
    "ODDS_API_IO_REJECT_NON_FULL_TIME_MARKETS": "true",
    # Context providers.
    "BZZOIRO_EVENTS_MAX_REQUESTS_PER_RUN": "1",
    "BZZOIRO_PREDICTIONS_MAX_REQUESTS_PER_RUN": "1",
    "BZZOIRO_PAGE_SIZE": "3",
    "BZZOIRO_TIMEOUT_SECONDS": "18",
    "SSTATS_MAX_REQUESTS_PER_RUN": "12",
    "FOOTBALL_DATA_MAX_REQUESTS_PER_RUN": "4",
    "THESPORTSDB_MAX_REQUESTS_PER_RUN": "4",
    "ALLSPORTSAPI_MAX_REQUESTS_PER_RUN": "1",
    "FUTRIXMETRICS_MAX_REQUESTS_PER_RUN": "6",
    "HIGHLIGHTLY_MAX_REQUESTS_PER_RUN": "4",
    "HIGHLIGHTLY_BASE_URL": "https://soccer.highlightly.net",
    "HIGHLIGHTLY_SMOKE_PATH": "/leagues",
    "FREE_FOOTBALL_RAPIDAPI_MAX_REQUESTS_PER_RUN": "2",
    # SportLogic broad fallback.
    "SPORTLOGIC_QUERY_GUARD_ENABLED": "true",
    "SPORTLOGIC_BROAD_FALLBACK_ENABLED": "true",
    "SPORTLOGIC_MAX_REQUESTS_PER_RUN": "8",
    "SPORTLOGIC_PER_PAGE": "100",
    # Weather.
    "WEATHERAPI_MAX_REQUESTS_PER_RUN": "40",
    "OPENWEATHERMAP_MAX_REQUESTS_PER_RUN": "20",
    "OPEN_METEO_ENABLED": "true",
    "OPEN_METEO_MAX_REQUESTS_PER_RUN": "60",
    "METEOSTAT_RAPIDAPI_MAX_REQUESTS_PER_RUN": "2",
    # News.
    "NEWSAPI_MAX_REQUESTS_PER_RUN": "2",
    "CURRENTS_MAX_REQUESTS_PER_RUN": "2",
    "GNEWS_MAX_REQUESTS_PER_RUN": "2",
    "NEWSDATA_MAX_REQUESTS_PER_RUN": "2",
    "GUARDIAN_MAX_REQUESTS_PER_RUN": "3",
    # Daily/no-key caches.
    "CLUBELO_ENABLED": "true",
    "CLUBELO_BASE_URL": "http://api.clubelo.com",
    "CLUBELO_MAX_REQUESTS_PER_DAY": "1",
    "FOOTBALL_DATA_CO_UK_ENABLED": "true",
    "FOOTBALL_DATA_CO_UK_MAX_REQUESTS_PER_DAY": "1",
    "WIKIDATA_ENABLED": "true",
    "WIKIDATA_MAX_REQUESTS_PER_DAY": "4",
    "WIKIDATA_SPARQL_MAX_REQUESTS_PER_DAY": "2",
    # Keep removed/problematic sources out of normal runtime.
    "ODDSPAPI_ENABLED": "false",
    "API_FOOTBALL_ENABLED": "false",
    "API_SPORTS_ENABLED": "false",
    "BOOKIES_API_ENABLED": "false",
    "SPORTAPI7_ENABLED": "false",
    "ODDS_FEED_RAPIDAPI_ENABLED": "false",
    "SPORTSBOOK_RAPIDAPI_ENABLED": "false",
}


def _truthy(value: Any, default: bool = False) -> bool:
    raw = str(value if value is not None else "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on", "force"}


def _load_policy() -> dict[str, Any]:
    try:
        if POLICY_PATH.exists():
            return json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return {}


def _apply_env_defaults() -> None:
    for key, value in ENV_DEFAULTS.items():
        os.environ.setdefault(key, value)


def _write_runtime_budget_snapshot(policy: dict[str, Any]) -> None:
    if not _truthy(os.getenv("RUNTIME_PROVIDER_DIAGNOSTICS_ENABLED"), True):
        return
    try:
        out_dir = Path(".data/exports")
        out_dir.mkdir(parents=True, exist_ok=True)
        providers = policy.get("providers") if isinstance(policy, dict) else None
        snapshot = {
            "enabled": True,
            "policy_version": policy.get("policy_version") if isinstance(policy, dict) else None,
            "runs_per_day_assumption": policy.get("runs_per_day_assumption") if isinstance(policy, dict) else None,
            "providers_count": len(providers or {}),
            "active_runtime_env_defaults": {key: os.getenv(key) for key in sorted(ENV_DEFAULTS)},
        }
        (out_dir / "latest-provider-runtime-budget.json").write_text(json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except Exception:
        pass


def install() -> bool:
    if not _truthy(os.getenv("PROVIDER_FREE_QUOTA_POLICY_ENABLED"), True):
        return False
    _apply_env_defaults()
    _write_runtime_budget_snapshot(_load_policy())
    return True
