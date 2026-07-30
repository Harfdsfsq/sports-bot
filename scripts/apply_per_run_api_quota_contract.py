from __future__ import annotations

"""Apply the HARIZON per-run API contract.

Normal runtime now maximizes useful lines/context from verified providers while
keeping publication strict. The contract keeps daily/monthly-limited or broken
providers off, but allows controlled secondary odds rescue from AllSportsAPI and
SportLogic when primary odds coverage is thin.
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

UTC = timezone.utc
ROOT = Path(".").resolve()
EXPORT_PATH = ROOT / ".data" / "exports" / "latest-per-run-api-quota-contract.json"
GITHUB_ENV = os.getenv("GITHUB_ENV")

ALLOWED_CORE = [
    "odds_api_io",
    "sstats",
    "bzzoiro",
    "football_data",
    "thesportsdb",
    "weatherapi",
    "open_meteo",
    "clubelo",
    "allsportsapi",
    "sportlogic",
]


def _present(*names: str) -> bool:
    return any(str(os.getenv(name) or "").strip() for name in names)


def _truthy(name: str, default: bool = False) -> bool:
    raw = str(os.getenv(name) or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on", "force"}


def _local_now() -> datetime:
    tz_name = os.getenv("APP_TIMEZONE") or os.getenv("TZ") or "Europe/Moscow"
    try:
        tz = ZoneInfo(str(tz_name))
    except Exception:
        tz = UTC
    return datetime.now(UTC).astimezone(tz)


def _phase() -> str:
    explicit = str(os.getenv("HARIZON_RUN_PHASE") or os.getenv("RUN_PHASE") or "").strip().lower()
    if explicit:
        return explicit
    hour = _local_now().hour
    if 0 <= hour <= 2:
        return "full_inventory"
    if 3 <= hour <= 10:
        return "morning_backfill"
    return "live_refresh"


def _put_limit(env: dict[str, str], provider_prefix: str, value: int, *extra_aliases: str) -> None:
    prefix = provider_prefix.upper()
    aliases = {
        f"{prefix}_PER_RUN_MAX",
        f"{prefix}_MAX_HTTP_REQUESTS_PER_RUN",
        f"{prefix}_MAX_REQUESTS_PER_RUN",
        f"{prefix}_REQUESTS_MAX_PER_RUN",
        f"{prefix}_REQUEST_BUDGET_GRANTED",
        *extra_aliases,
    }
    for alias in aliases:
        env[alias] = str(max(0, int(value)))


def _disable_provider(env: dict[str, str], prefix: str, reason: str) -> None:
    upper = prefix.upper()
    _put_limit(env, upper, 0)
    env[f"{upper}_REQUEST_BUDGET_REASON"] = reason
    for key in (f"ENABLE_{upper}", f"{upper}_ENABLED"):
        env[key] = "false"


def _provider_contract(phase: str) -> tuple[dict[str, str], dict[str, Any]]:
    if phase == "full_inventory":
        odds_total, odds_account = 170, 85
        max_matches_for_odds, analysis_cap = 900, 900
        context_limit, premium_context = 300, 140
        bzzoiro, sstats, football_data, thesportsdb = 18, 36, 8, 14
        weatherapi, open_meteo, clubelo = 8, 80, 3
        allsportsapi, sportlogic = 8, 8
    elif phase == "morning_backfill":
        odds_total, odds_account = 150, 75
        max_matches_for_odds, analysis_cap = 700, 700
        context_limit, premium_context = 260, 120
        bzzoiro, sstats, football_data, thesportsdb = 20, 34, 8, 12
        weatherapi, open_meteo, clubelo = 8, 80, 3
        allsportsapi, sportlogic = 6, 6
    else:
        odds_total, odds_account = 130, 65
        max_matches_for_odds, analysis_cap = 560, 560
        context_limit, premium_context = 260, 120
        bzzoiro, sstats, football_data, thesportsdb = 20, 32, 6, 10
        weatherapi, open_meteo, clubelo = 8, 80, 3
        allsportsapi, sportlogic = 4, 4

    odds_key_present = _present("ODDS_API_IO_KEY")
    odds_key2_present = _present("ODDS_API_IO_KEY_2", "ODDS_API_IO_KEY2")
    env: dict[str, str] = {
        "HARIZON_API_QUOTA_CONTRACT_VERSION": "v4-max-lines-context-2026-05-09",
        "HARIZON_RUN_PHASE_EFFECTIVE": phase,
        "HARIZON_ALLOWED_PROVIDER_SET": ",".join(ALLOWED_CORE),
        "HARIZON_PROVIDER_PROBE_MODE": "false",
        "PROVIDER_REQUEST_BUDGET_MODE": "per_run_only",
        "PROVIDER_REQUEST_BUDGET_DISABLE_DAILY_MONTHLY": "true",
        "ALL_SOURCES_FREE_MAXIMIZE": "true",
        "RUN_DAYS_AHEAD": "1",
        "PUBLISH_WINDOW_HOURS": "24" if phase == "full_inventory" else "12",
        "MAX_MATCHES_FOR_ODDS_FETCH": str(max_matches_for_odds),
        "ANALYSIS_MATCH_CAP_PER_RUN": str(analysis_cap),
        "DIAGNOSTICS_MATCH_LIMIT": str(analysis_cap),
        "CONTEXT_ENRICHMENT_MATCH_LIMIT": str(context_limit),
        "PREMIUM_CONTEXT_SHORTLIST_LIMIT": str(premium_context),
        # Context should be collected for near-window inventory matches even if
        # odds are not yet matched. Publication still requires strict market guards.
        "CONTEXT_ENRICHMENT_REQUIRES_OFFERS": "false",
        "DAY_INVENTORY_CONTEXT_BACKFILL_ENABLED": "true",
        "DAY_INVENTORY_CONTEXT_BACKFILL_LIMIT": "160",
        "DAY_INVENTORY_USE_FOR_RUN": "true",
        "DAY_INVENTORY_NEAR_WINDOW_PRIORITY": "true",
        "DAY_INVENTORY_NEAR_WINDOW_HOURS": "12",
        "DAY_INVENTORY_BOOTSTRAP_PROVIDER": "odds_api_io",
        "MATCH_BOOTSTRAP_PROVIDER": "odds_api_io",
        "DAY_INVENTORY_FORCE_PROVIDER_MERGE": "false",
        "DAY_INVENTORY_COVERAGE_MAX_REBUILD": "false",
        # Secondary odds rescue is controlled and does not relax publication.
        "SECONDARY_ODDS_RESCUE_ENABLED": "true",
        "SECONDARY_ODDS_RESCUE_TRIGGER": "odds_api_io_empty_or_thin",
        "SECONDARY_ODDS_RESCUE_MIN_PRIMARY_OFFERS": "80",
        "SECONDARY_ODDS_RESCUE_NEAR_WINDOW_HOURS": "12",
        "PROVIDER_CONTEXT_SOURCES_DO_NOT_CONFIRM_PRICE": "true",
        "PUBLISH_ALLOW_B_TIER": "true",
        "PUBLISH_COVERAGE_TIER_MODE": "hybrid",
        "CONTROLLED_FALLBACK_TELEGRAM_ALLOW_TIER_B": "true",
        "MIN_BOOKS_FOR_CONSENSUS": "2",
        "MIN_BOOKS_PUBLISH": "2",
        "MIN_SOURCES_PUBLISH": "1",
        "MARKET_DERIVED_MIN_BOOKS": "2",
        "MARKET_DERIVED_MIN_SOURCES": "1",
        "CONTROLLED_FALLBACK_MIN_INDEPENDENT_SOURCES": "1",
        "CONTROLLED_FALLBACK_MIN_ODDS_SOURCES": "1",
        "CONTROLLED_FALLBACK_REQUIRE_2_BOOKS_FOR_TELEGRAM": "false",
        "CONTROLLED_FALLBACK_REQUIRE_2_ODDS_SOURCES_FOR_TELEGRAM": "false",
        "CONTROLLED_FALLBACK_REJECT_SINGLE_SOURCE_UNLESS_3_BOOKS": "true",
        "TELEGRAM_MAIN_PICK_MIN_ODDS_SOURCES": "1",
        "TELEGRAM_MAIN_PICK_MIN_EDGE_PP": "3.0",
        "TELEGRAM_MAIN_PICK_STRICT_SINGLE_SOURCE": "false",
        "TELEGRAM_MAIN_PICK_SINGLE_SOURCE_MIN_BOOKS": "3",
        "TELEGRAM_MAIN_PICK_SINGLE_SOURCE_MIN_EDGE_PP": "4.0",
        "TELEGRAM_MAIN_PICK_SINGLE_SOURCE_MIN_EV_PCT": "8.0",
        "TELEGRAM_MAIN_PICK_SINGLE_SOURCE_MIN_CONFIDENCE": "78.0",
        "TELEGRAM_MAIN_PICK_SINGLE_SOURCE_MIN_QUALITY": "78.0",
        "ODDS_API_IO_RELAXED_INVENTORY_MATCHING_ENABLED": "true",
        "ODDS_API_IO_RELAXED_EXACT_TOLERANCE_HOURS": "16",
        "ODDS_API_IO_RELAXED_FUZZY_TOLERANCE_HOURS": "16",
        "ODDS_API_IO_RELAXED_MIN_SCORE": "42",
        "ENABLE_ODDS_API_IO": "true",
        "ODDS_API_IO_ENABLED": "true",
        "ODDS_API_IO_BOOKMAKERS": "Bet365,Unibet,William Hill,Bwin",
        "ODDS_API_IO_BOOKMAKERS_ACCOUNT1": "Bet365,Unibet",
        "ODDS_API_IO_BOOKMAKERS_ACCOUNT2": "William Hill,Bwin",
        "TARGET_BOOKMAKERS": "Bet365,Unibet,William Hill,Bwin,Betfair Exchange,Sbobet",
        "CONSENSUS_BOOKMAKERS": "Bet365,Unibet,William Hill,Bwin,Betfair Exchange,Sbobet",
        "ODDS_API_IO_PAGE_LIMIT": "100",
        "ODDS_API_IO_MAX_EVENT_PAGES_PER_SPORT": "36" if phase == "full_inventory" else "24" if phase == "morning_backfill" else "18",
        "ODDS_API_IO_ACCOUNT1_PER_RUN_MAX": str(odds_account if odds_key_present else 0),
        "ODDS_API_IO_ACCOUNT2_PER_RUN_MAX": str(odds_account if odds_key2_present else 0),
        "ODDS_API_IO_ACCOUNT1_MAX_REQUESTS_PER_RUN": str(odds_account if odds_key_present else 0),
        "ODDS_API_IO_ACCOUNT2_MAX_REQUESTS_PER_RUN": str(odds_account if odds_key2_present else 0),
    }
    _put_limit(env, "ODDS_API_IO", odds_total if odds_key_present else 0)

    env.update({
        "ENABLE_SSTATS_CONTEXT": "true" if _present("SSTATS_API_KEY") else "false",
        "ENABLE_SSTATS": "true" if _present("SSTATS_API_KEY") else "false",
        "SSTATS_ENABLED": "true" if _present("SSTATS_API_KEY") else "false",
        "SSTATS_RECENT_MATCHES": "10",
        "SSTATS_LOOKBACK_DAYS": "45",
        "SSTATS_CONTEXT_MATCH_LIMIT": "120",
    })
    _put_limit(env, "SSTATS", sstats if _present("SSTATS_API_KEY") else 0)

    env.update({
        "ENABLE_BZZOIRO_CONTEXT": "true" if _present("BZZOIRO_API_KEY") else "false",
        "ENABLE_BZZOIRO": "true" if _present("BZZOIRO_API_KEY") else "false",
        "BZZOIRO_ENABLED": "true" if _present("BZZOIRO_API_KEY") else "false",
        "BZZOIRO_CONTEXT_MATCH_LIMIT": "96",
        "BZZOIRO_MAX_PAGES": "8",
        "BZZOIRO_PAGE_SIZE": "10",
    })
    _put_limit(env, "BZZOIRO", bzzoiro if _present("BZZOIRO_API_KEY") else 0, "BZZOIRO_EVENTS_MAX_REQUESTS_PER_RUN", "BZZOIRO_PREDICTIONS_MAX_REQUESTS_PER_RUN")

    env.update({
        "ENABLE_FOOTBALL_DATA_CONTEXT": "true" if _present("FOOTBALL_DATA_API_KEY", "FOOTBALL_DATA_KEY") else "false",
        "FOOTBALL_DATA_ENABLED": "true" if _present("FOOTBALL_DATA_API_KEY", "FOOTBALL_DATA_KEY") else "false",
        "FOOTBALL_DATA_CONTEXT_MATCH_LIMIT": "120",
        "FOOTBALL_DATA_CACHE_TTL_MINUTES": "720",
    })
    _put_limit(env, "FOOTBALL_DATA", football_data if _present("FOOTBALL_DATA_API_KEY", "FOOTBALL_DATA_KEY") else 0)

    env.update({
        "ENABLE_THESPORTSDB_CONTEXT": "true",
        "THESPORTSDB_CONTEXT_ENABLED": "true",
        "THESPORTSDB_ENABLED": "true",
        "THESPORTSDB_API_KEY": os.getenv("THESPORTSDB_API_KEY") or "123",
        "THESPORTSDB_CONTEXT_MATCH_LIMIT": "120",
    })
    _put_limit(env, "THESPORTSDB", thesportsdb)

    env.update({
        "WEATHER_CONTEXT_ENABLED": "true",
        "WEATHER_CONTEXT_MATCH_LIMIT": str(weatherapi),
        "ENABLE_WEATHERAPI": "true" if _present("WEATHERAPI_KEY", "WEATHER_API_KEY", "WEATHERAPI_API_KEY") else "false",
        "WEATHERAPI_ENABLED": "true" if _present("WEATHERAPI_KEY", "WEATHER_API_KEY", "WEATHERAPI_API_KEY") else "false",
        "OPEN_METEO_ENABLED": "true",
        "ENABLE_OPEN_METEO": "true",
        "CLUBELO_ENABLED": "true",
        "ENABLE_CLUBELO": "true",
    })
    _put_limit(env, "WEATHERAPI", weatherapi if _present("WEATHERAPI_KEY", "WEATHER_API_KEY", "WEATHERAPI_API_KEY") else 0)
    _put_limit(env, "OPEN_METEO", open_meteo)
    _put_limit(env, "CLUBELO", clubelo)

    env.update({
        "ENABLE_ALLSPORTSAPI": "true" if _present("ALLSPORTSAPI_API_KEY") else "false",
        "ALLSPORTSAPI_ENABLED": "true" if _present("ALLSPORTSAPI_API_KEY") else "false",
        "ALLSPORTSAPI_ONLY_IF_PRIMARY_ODDS_EMPTY": "true",
        "ALLSPORTSAPI_MATCH_LIMIT": "40",
    })
    _put_limit(env, "ALLSPORTSAPI", allsportsapi if _present("ALLSPORTSAPI_API_KEY") else 0)

    sportlogic_enabled = _present(
        "SPORTLOGIC_API_KEY",
        "SPORTLOGIC_KEY",
        "SPORTLOGIC_TOKEN",
    )
    env.update({
        "ENABLE_SPORTLOGIC": "true" if sportlogic_enabled else "false",
        "SPORTLOGIC_ENABLED": "true" if sportlogic_enabled else "false",
        "SPORTLOGIC_CONTROLLED_ODDS_ENABLED": "true" if sportlogic_enabled else "false",
        "SPORTLOGIC_ONLY_IF_PRIMARY_ODDS_EMPTY": "false",
        "SPORTLOGIC_MATCH_LIMIT": "40" if sportlogic_enabled else "0",
        "SPORTLOGIC_CONTEXT_MATCH_LIMIT": "60" if sportlogic_enabled else "0",
        "SPORTLOGIC_ODDS_MATCH_LIMIT": "20" if sportlogic_enabled else "0",
        "SPORTLOGIC_MIN_SECONDS_BETWEEN_REQUESTS": "7",
    })
    _put_limit(env, "SPORTLOGIC", sportlogic if sportlogic_enabled else 0)

    disabled = {
        "OPENWEATHERMAP": "not_in_allowed_core_use_weatherapi_openmeteo",
        "ODDSPAPI": "not_in_allowed_core_monthly_limited_unverified",
        "RAPIDAPI_ODDS_FEED": "not_in_allowed_core_unverified",
        "HIGHLIGHTLY": "not_in_allowed_core_unverified",
        "API_FOOTBALL": "not_in_allowed_core_daily_limited",
        "FUTRIXMETRICS": "not_in_allowed_core_zero_context_yield",
        "NEWSAPI": "not_in_allowed_core_news_not_runtime",
        "CURRENTS": "not_in_allowed_core_news_not_runtime",
        "GNEWS": "not_in_allowed_core_news_not_runtime",
        "NEWSDATA": "not_in_allowed_core_news_not_runtime",
        "GUARDIAN": "not_in_allowed_core_news_not_runtime",
        "METEOSTAT": "not_in_allowed_core_weatherapi_openmeteo_first",
        "RAPIDAPI_SPORTSBOOK": "not_in_allowed_core_daily_limit_too_small",
        "RAPIDAPI_FREE_FOOTBALL": "not_in_allowed_core_monthly_limit_too_small",
        "SHARPAPI": "not_in_allowed_core_text_enrichment",
        "OPENFOOTBALL": "not_in_allowed_core",
        "WIKIDATA": "not_in_allowed_core",
        "FOOTBALL_DATA_CO_UK": "not_in_allowed_core",
        "BOOKIES_API": "removed_from_project",
        "SPORTAPI": "not_in_allowed_core",
        "FREEAPILIVEFOOTBALL": "not_in_allowed_core",
    }
    for prefix, reason in disabled.items():
        _disable_provider(env, prefix, reason)
    env["WIKIDATA_MAX_REQUESTS_PER_DAY"] = "0"
    env["WIKIDATA_SPARQL_MAX_REQUESTS_PER_DAY"] = "0"
    env["OPENWEATHERMAP_MAX_HTTP_REQUESTS_PER_RUN"] = "0"
    env["OPENWEATHERMAP_PER_RUN_MAX"] = "0"
    env["WEATHER_OPENWEATHERMAP_FALLBACK_ENABLED"] = "false"

    contract = {
        "phase": phase,
        "allowed_core_providers": ALLOWED_CORE,
        "disabled_providers": disabled,
        "free_limits_reference": "api_free_limits_ru.pdf/docx",
        "per_run_grants": {
            "odds_api_io": odds_total if odds_key_present else 0,
            "odds_api_io_account1": odds_account if odds_key_present else 0,
            "odds_api_io_account2": odds_account if odds_key2_present else 0,
            "sstats": sstats if _present("SSTATS_API_KEY") else 0,
            "bzzoiro": bzzoiro if _present("BZZOIRO_API_KEY") else 0,
            "football_data": football_data if _present("FOOTBALL_DATA_API_KEY", "FOOTBALL_DATA_KEY") else 0,
            "thesportsdb": thesportsdb,
            "weatherapi": weatherapi if _present("WEATHERAPI_KEY", "WEATHER_API_KEY", "WEATHERAPI_API_KEY") else 0,
            "open_meteo": open_meteo,
            "clubelo": clubelo,
            "allsportsapi": allsportsapi if _present("ALLSPORTSAPI_API_KEY") else 0,
            "sportlogic": sportlogic if _present("SPORTLOGIC_API_KEY", "SPORTLOGIC_KEY", "SPORTLOGIC_TOKEN") else 0,
        },
        "notes": [
            "Runtime maximizes verified odds/context coverage while publication remains strict.",
            "Odds API IO inventory source IDs are used for exact odds matching before fuzzy matching.",
            "Context no longer requires already-matched offers; near-window inventory rows are enriched first.",
            "AllSportsAPI and SportLogic are controlled secondary odds rescue providers only.",
        ],
    }
    return env, contract


def _write_env(env: dict[str, str]) -> None:
    for key, value in env.items():
        os.environ[str(key)] = str(value)
    if not GITHUB_ENV:
        for key in sorted(env):
            print(f"{key}={env[key]}")
        return
    with open(GITHUB_ENV, "a", encoding="utf-8") as fh:
        for key in sorted(env):
            fh.write(f"{key}={env[key]}\n")


def main() -> int:
    phase = _phase()
    env, contract = _provider_contract(phase)
    _write_env(env)
    EXPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    contract["created_at_utc"] = datetime.now(UTC).isoformat()
    contract["env_written_count"] = len(env)
    EXPORT_PATH.write_text(json.dumps(contract, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(contract, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
