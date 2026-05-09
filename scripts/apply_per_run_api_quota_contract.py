from __future__ import annotations

"""Apply the HARIZON top-provider per-run API contract.

Runtime policy after live evidence:
- CORE providers run every normal run because they repeatedly produce useful data.
- WATCHLIST providers are cheap/cache/fallback only.
- QUARANTINE providers are disabled in normal runs until a parser/matching fix is
  tested with HARIZON_PROVIDER_PROBE_MODE=true.

This script is the final GitHub Actions env layer after quota governor and base
request budget. It writes every common alias for per-run limits because different
providers read different setting names.
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


def _present(*names: str) -> bool:
    return any(str(os.getenv(name) or "").strip() for name in names)


def _truthy(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on", "force"}


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
    probe_mode = _truthy("HARIZON_PROVIDER_PROBE_MODE", False)

    if phase == "full_inventory":
        odds_total, odds_account = 160, 80
        max_matches_for_odds, analysis_cap = 900, 900
        context_limit, premium_context = 120, 48
        bzzoiro, sstats, football_data, thesportsdb = 10, 18, 6, 12
        weatherapi, openweathermap, open_meteo = 6, 0, 80
    elif phase == "morning_backfill":
        odds_total, odds_account = 140, 70
        max_matches_for_odds, analysis_cap = 650, 650
        context_limit, premium_context = 240, 84
        bzzoiro, sstats, football_data, thesportsdb = 16, 28, 6, 10
        weatherapi, openweathermap, open_meteo = 8, 2, 80
    else:
        odds_total, odds_account = 120, 60
        max_matches_for_odds, analysis_cap = 520, 520
        context_limit, premium_context = 220, 84
        bzzoiro, sstats, football_data, thesportsdb = 18, 30, 4, 8
        weatherapi, openweathermap, open_meteo = 8, 2, 80

    # Quarantined sources: they have secrets/free limits, but live runs show
    # zero matched offers or zero useful contexts. They must not spend quota in
    # normal runs. Probe mode gives tiny controlled budgets for parser work.
    allsportsapi = 2 if probe_mode and _present("ALLSPORTSAPI_API_KEY", "ALLSPORTSAPI_KEY") else 0
    sportlogic = 2 if probe_mode and _present("SPORTLOGIC_API_KEY", "SPORTLOGIC_KEY", "SPORTLOGIC_TOKEN") else 0
    oddspapi = 1 if probe_mode and _present("ODDSPAPI_API_KEY", "ODDSPAPI_KEY", "ODDS_PAPI_API_KEY") else 0
    oddsfeed = 1 if probe_mode and _present("ODDS_FEED_RAPIDAPI_KEY", "RAPIDAPI_KEY") else 0
    highlightly = 1 if probe_mode and _present("HIGHLIGHTLY_API_KEY") else 0

    env: dict[str, str] = {
        "HARIZON_API_QUOTA_CONTRACT_VERSION": "v2-top-providers-core-only-2026-05-09",
        "HARIZON_RUN_PHASE_EFFECTIVE": phase,
        "HARIZON_PROVIDER_PROBE_MODE": "true" if probe_mode else "false",
        "PROVIDER_REQUEST_BUDGET_MODE": "per_run_only",
        "PROVIDER_REQUEST_BUDGET_DISABLE_DAILY_MONTHLY": "true",
        "ALL_SOURCES_FREE_MAXIMIZE": "false",
        "RUN_DAYS_AHEAD": "1",
        "PUBLISH_WINDOW_HOURS": "24" if phase == "full_inventory" else "12",
        "MAX_MATCHES_FOR_ODDS_FETCH": str(max_matches_for_odds),
        "ANALYSIS_MATCH_CAP_PER_RUN": str(analysis_cap),
        "DIAGNOSTICS_MATCH_LIMIT": str(analysis_cap),
        "CONTEXT_ENRICHMENT_MATCH_LIMIT": str(context_limit),
        "PREMIUM_CONTEXT_SHORTLIST_LIMIT": str(premium_context),
        "CONTEXT_ENRICHMENT_REQUIRES_OFFERS": "true",
        "DAY_INVENTORY_USE_FOR_RUN": "true",
        "DAY_INVENTORY_NEAR_WINDOW_PRIORITY": "true",
        "DAY_INVENTORY_NEAR_WINDOW_HOURS": "12",
        "DAY_INVENTORY_BOOTSTRAP_PROVIDER": "odds_api_io",
        "MATCH_BOOTSTRAP_PROVIDER": "odds_api_io",
        "DAY_INVENTORY_FORCE_PROVIDER_MERGE": "true" if phase == "full_inventory" else "false",
        "DAY_INVENTORY_COVERAGE_MAX_REBUILD": "true" if phase == "full_inventory" else "false",
        "SECONDARY_ODDS_RESCUE_ENABLED": "true" if probe_mode else "false",
        "SECONDARY_ODDS_RESCUE_TRIGGER": "probe_mode_only" if probe_mode else "disabled_until_secondary_parser_fixed",
        "SECONDARY_ODDS_RESCUE_MIN_PRIMARY_OFFERS": "40",
        "SECONDARY_ODDS_RESCUE_NEAR_WINDOW_HOURS": "12",
        "PROVIDER_CONTEXT_SOURCES_DO_NOT_CONFIRM_PRICE": "true",
        "MIN_BOOKS_FOR_CONSENSUS": "2",
        "MIN_BOOKS_PUBLISH": "2",
        "MIN_SOURCES_PUBLISH": "2",
        "MARKET_DERIVED_MIN_BOOKS": "2",
        "MARKET_DERIVED_MIN_SOURCES": "2",
        "CONTROLLED_FALLBACK_MIN_INDEPENDENT_SOURCES": "2",
        "CONTROLLED_FALLBACK_MIN_ODDS_SOURCES": "2",
        "CONTROLLED_FALLBACK_REQUIRE_2_BOOKS_FOR_TELEGRAM": "true",
        "CONTROLLED_FALLBACK_REQUIRE_2_ODDS_SOURCES_FOR_TELEGRAM": "true",
        "CONTROLLED_FALLBACK_REJECT_SINGLE_SOURCE_UNLESS_3_BOOKS": "true",
        "TELEGRAM_MAIN_PICK_MIN_ODDS_SOURCES": "2",
        "TELEGRAM_MAIN_PICK_MIN_EDGE_PP": "3.0",
        "TELEGRAM_MAIN_PICK_STRICT_SINGLE_SOURCE": "true",
        "TELEGRAM_MAIN_PICK_SINGLE_SOURCE_MIN_BOOKS": "3",
        "TELEGRAM_MAIN_PICK_SINGLE_SOURCE_MIN_EDGE_PP": "4.0",
        "TELEGRAM_MAIN_PICK_SINGLE_SOURCE_MIN_EV_PCT": "8.0",
        "TELEGRAM_MAIN_PICK_SINGLE_SOURCE_MIN_CONFIDENCE": "78.0",
        "TELEGRAM_MAIN_PICK_SINGLE_SOURCE_MIN_QUALITY": "78.0",
        "ENABLE_ODDS_API_IO": "true",
        "ODDS_API_IO_ENABLED": "true",
        "ODDS_API_IO_BOOKMAKERS": "Bet365,Unibet,Betfair Exchange,Sbobet",
        "ODDS_API_IO_BOOKMAKERS_ACCOUNT1": "Bet365,Unibet",
        "ODDS_API_IO_BOOKMAKERS_ACCOUNT2": "Betfair Exchange,Sbobet",
        "TARGET_BOOKMAKERS": "Bet365,Unibet,Betfair Exchange,Sbobet",
        "CONSENSUS_BOOKMAKERS": "Bet365,Unibet,Betfair Exchange,Sbobet",
        "ODDS_API_IO_PAGE_LIMIT": "100",
        "ODDS_API_IO_MAX_EVENT_PAGES_PER_SPORT": "36" if phase == "full_inventory" else "24" if phase == "morning_backfill" else "18",
        "ODDS_API_IO_ACCOUNT1_PER_RUN_MAX": str(odds_account if _present("ODDS_API_IO_KEY") else 0),
        "ODDS_API_IO_ACCOUNT2_PER_RUN_MAX": str(odds_account if _present("ODDS_API_IO_KEY_2", "ODDS_API_IO_KEY2") else 0),
        "ODDS_API_IO_ACCOUNT1_MAX_REQUESTS_PER_RUN": str(odds_account if _present("ODDS_API_IO_KEY") else 0),
        "ODDS_API_IO_ACCOUNT2_MAX_REQUESTS_PER_RUN": str(odds_account if _present("ODDS_API_IO_KEY_2", "ODDS_API_IO_KEY2") else 0),
    }
    _put_limit(env, "ODDS_API_IO", odds_total if _present("ODDS_API_IO_KEY") else 0)

    # CORE: produces useful data repeatedly.
    env.update({"ENABLE_SSTATS_CONTEXT": "true" if _present("SSTATS_API_KEY") else "false", "SSTATS_ENABLED": "true" if _present("SSTATS_API_KEY") else "false", "SSTATS_RECENT_MATCHES": "10", "SSTATS_LOOKBACK_DAYS": "45", "SSTATS_CONTEXT_MATCH_LIMIT": "72"})
    _put_limit(env, "SSTATS", sstats if _present("SSTATS_API_KEY") else 0)
    env.update({"ENABLE_BZZOIRO_CONTEXT": "true" if _present("BZZOIRO_API_KEY") else "false", "BZZOIRO_ENABLED": "true" if _present("BZZOIRO_API_KEY") else "false", "BZZOIRO_CONTEXT_MATCH_LIMIT": "60", "BZZOIRO_MAX_PAGES": "5", "BZZOIRO_PAGE_SIZE": "10"})
    _put_limit(env, "BZZOIRO", bzzoiro if _present("BZZOIRO_API_KEY") else 0, "BZZOIRO_EVENTS_MAX_REQUESTS_PER_RUN", "BZZOIRO_PREDICTIONS_MAX_REQUESTS_PER_RUN")
    env.update({"ENABLE_FOOTBALL_DATA_CONTEXT": "true" if _present("FOOTBALL_DATA_API_KEY", "FOOTBALL_DATA_KEY") else "false", "FOOTBALL_DATA_ENABLED": "true" if _present("FOOTBALL_DATA_API_KEY", "FOOTBALL_DATA_KEY") else "false", "FOOTBALL_DATA_CONTEXT_MATCH_LIMIT": "48", "FOOTBALL_DATA_CACHE_TTL_MINUTES": "720"})
    _put_limit(env, "FOOTBALL_DATA", football_data if _present("FOOTBALL_DATA_API_KEY", "FOOTBALL_DATA_KEY") else 0)
    env.update({"ENABLE_THESPORTSDB_CONTEXT": "true", "THESPORTSDB_CONTEXT_ENABLED": "true", "THESPORTSDB_API_KEY": os.getenv("THESPORTSDB_API_KEY") or "123", "THESPORTSDB_CONTEXT_MATCH_LIMIT": "72"})
    _put_limit(env, "THESPORTSDB", thesportsdb)

    # WATCHLIST/FALLBACK: only cheap and only when useful.
    env.update({"WEATHER_CONTEXT_ENABLED": "true", "WEATHER_CONTEXT_MATCH_LIMIT": str(weatherapi), "ENABLE_WEATHERAPI": "true" if _present("WEATHERAPI_KEY", "WEATHER_API_KEY", "WEATHERAPI_API_KEY") else "false", "WEATHERAPI_ENABLED": "true" if _present("WEATHERAPI_KEY", "WEATHER_API_KEY", "WEATHERAPI_API_KEY") else "false"})
    _put_limit(env, "WEATHERAPI", weatherapi if _present("WEATHERAPI_KEY", "WEATHER_API_KEY", "WEATHERAPI_API_KEY") else 0)
    _put_limit(env, "OPENWEATHERMAP", openweathermap if _present("OPENWEATHERMAP_API_KEY", "OPENWEATHER_API_KEY", "OPENWEATHERMAP_KEY") else 0)
    _put_limit(env, "OPEN_METEO", open_meteo)
    env.update({"CLUBELO_ENABLED": "true", "FOOTBALL_DATA_CO_UK_ENABLED": "true", "WIKIDATA_ENABLED": "false", "WIKIDATA_MAX_REQUESTS_PER_DAY": "0", "WIKIDATA_SPARQL_MAX_REQUESTS_PER_DAY": "0"})

    # QUARANTINE: zero-yield or monthly-limited providers. Tiny grants only in probe mode.
    env.update({"ENABLE_ALLSPORTSAPI": "true" if allsportsapi else "false", "ALLSPORTSAPI_ENABLED": "true" if allsportsapi else "false", "ALLSPORTSAPI_MATCH_LIMIT": "8", "ALLSPORTSAPI_ONLY_IF_PRIMARY_ODDS_EMPTY": "false"})
    _put_limit(env, "ALLSPORTSAPI", allsportsapi)
    env.update({"ENABLE_SPORTLOGIC": "true" if sportlogic else "false", "SPORTLOGIC_ENABLED": "true" if sportlogic else "false", "SPORTLOGIC_CONTROLLED_ODDS_ENABLED": "true" if sportlogic else "false", "SPORTLOGIC_ODDS_MATCH_LIMIT": "4", "SPORTLOGIC_CONTEXT_MATCH_LIMIT": "8", "SPORTLOGIC_MATCH_LIMIT": "8", "SPORTLOGIC_PER_PAGE": "50", "SPORTLOGIC_MIN_SECONDS_BETWEEN_REQUESTS": "7", "SPORTLOGIC_ONLY_IF_PRIMARY_ODDS_EMPTY": "false"})
    _put_limit(env, "SPORTLOGIC", sportlogic)
    env.update({"ENABLE_ODDSPAPI": "true" if oddspapi else "false", "ODDSPAPI_ENABLED": "true" if oddspapi else "false", "ODDSPAPI_MATCH_LIMIT": "4", "ODDSPAPI_CONTEXT_MATCH_LIMIT": "0", "ODDSPAPI_ONLY_IF_PRIMARY_ODDS_EMPTY": "false"})
    _put_limit(env, "ODDSPAPI", oddspapi)
    _put_limit(env, "RAPIDAPI_ODDS_FEED", oddsfeed)
    _put_limit(env, "HIGHLIGHTLY", highlightly)

    # OFF: not core for prediction quality or historically zero-yield.
    for prefix, reason in {
        "API_FOOTBALL": "no_secret_or_daily_limited_not_core_live_runtime",
        "FUTRIXMETRICS": "zero_context_yield_until_mapping_fixed",
        "NEWSAPI": "news_not_core_prediction_runtime",
        "CURRENTS": "news_not_core_prediction_runtime",
        "GNEWS": "news_not_core_prediction_runtime",
        "NEWSDATA": "news_not_core_prediction_runtime",
        "GUARDIAN": "news_not_core_prediction_runtime",
        "METEOSTAT": "weatherapi_openmeteo_first",
        "RAPIDAPI_SPORTSBOOK": "daily_limit_too_small_and_not_integrated",
        "RAPIDAPI_FREE_FOOTBALL": "monthly_limit_too_small",
        "SHARPAPI": "text_enrichment_not_core_prediction",
    }.items():
        _disable_provider(env, prefix, reason)

    contract = {
        "phase": phase,
        "provider_probe_mode": probe_mode,
        "free_limits_reference": "api_free_limits_ru.pdf/docx",
        "tiers": {
            "core_every_run": ["odds_api_io", "sstats", "bzzoiro", "football_data", "thesportsdb"],
            "watchlist_fallback": ["weatherapi", "openweathermap", "open_meteo", "clubelo", "football_data_co_uk"],
            "quarantine_probe_only": ["allsportsapi", "sportlogic", "oddspapi", "oddsfeed", "highlightly"],
            "off": ["api_football", "futrixmetrics", "newsapi", "currents", "gnews", "newsdata", "guardian", "meteostat", "sportsbook_api", "freeapilivefootball", "sharpapi"],
        },
        "per_run_grants": {
            "odds_api_io": odds_total if _present("ODDS_API_IO_KEY") else 0,
            "odds_api_io_account1": odds_account if _present("ODDS_API_IO_KEY") else 0,
            "odds_api_io_account2": odds_account if _present("ODDS_API_IO_KEY_2", "ODDS_API_IO_KEY2") else 0,
            "sstats": sstats if _present("SSTATS_API_KEY") else 0,
            "bzzoiro": bzzoiro if _present("BZZOIRO_API_KEY") else 0,
            "football_data": football_data if _present("FOOTBALL_DATA_API_KEY", "FOOTBALL_DATA_KEY") else 0,
            "thesportsdb": thesportsdb,
            "weatherapi": weatherapi if _present("WEATHERAPI_KEY", "WEATHER_API_KEY", "WEATHERAPI_API_KEY") else 0,
            "openweathermap": openweathermap if _present("OPENWEATHERMAP_API_KEY", "OPENWEATHER_API_KEY", "OPENWEATHERMAP_KEY") else 0,
            "open_meteo": open_meteo,
            "allsportsapi": allsportsapi,
            "sportlogic": sportlogic,
            "oddspapi": oddspapi,
            "oddsfeed": oddsfeed,
            "highlightly": highlightly,
        },
        "notes": [
            "Core providers are the only normal-run spenders.",
            "AllSportsAPI and SportLogic are quarantined because latest runs show fixtures/games but 0 matched offers.",
            "Monthly-limited odds APIs are probe-only until their endpoint schema is verified.",
            "News APIs are off for normal prediction runs; they can be reintroduced only for special top-match news risk.",
            "Publication still prefers 2 odds sources and at least 2 bookmakers; single-source is exception-only.",
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
