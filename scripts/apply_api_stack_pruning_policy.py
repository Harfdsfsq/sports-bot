from __future__ import annotations

"""Apply the active HARIZON API stack policy.

The bot previously kept too many low-yield providers in the normal runtime path.
This layer makes the runtime explicit:

Core every-run providers:
- odds-api.io, SStats, Bzzoiro, ClubElo
- football-data.org, TheSportsDB
- WeatherAPI + Open-Meteo

Controlled/fallback providers:
- AllSportsAPI for fixture/alias rescue only
- OpenWeatherMap and Meteostat only as weather fallbacks/probes
- News providers only for shortlist, not global context

Frozen until parser/mapping is repaired:
- SportLogic, FutrixMetrics, OddsPapi, Sportsbook API, FreeAPILiveFootballData,
  SportAPI/API-Sports overlap, OddsFeed, Highlightly.
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(".").resolve()
EXPORT_PATH = ROOT / ".data" / "exports" / "latest-api-stack-pruning-policy.json"
GITHUB_ENV = os.getenv("GITHUB_ENV")
POLICY_VERSION = "api-stack-pruning-v1-core-lines-context-2026-05-10"


def _truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on", "force"}


def _present(*names: str) -> bool:
    return any(str(os.getenv(name) or "").strip() for name in names)


def _put(env: dict[str, str], key: str, value: Any) -> None:
    env[str(key)] = str(value)


def _put_limit(env: dict[str, str], prefix: str, value: int) -> None:
    p = prefix.upper()
    for suffix in (
        "PER_RUN_MAX",
        "MAX_HTTP_REQUESTS_PER_RUN",
        "MAX_REQUESTS_PER_RUN",
        "REQUESTS_MAX_PER_RUN",
        "REQUEST_BUDGET_GRANTED",
    ):
        env[f"{p}_{suffix}"] = str(max(0, int(value)))


def _disable(env: dict[str, str], prefix: str, reason: str) -> None:
    p = prefix.upper()
    for key in (f"ENABLE_{p}", f"{p}_ENABLED", f"{p}_CONTEXT_ENABLED", f"ENABLE_{p}_CONTEXT"):
        env[key] = "false"
    _put_limit(env, p, 0)
    env[f"{p}_CONTEXT_MATCH_LIMIT"] = "0"
    env[f"{p}_MATCH_LIMIT"] = "0"
    env[f"{p}_ODDS_MATCH_LIMIT"] = "0"
    env[f"{p}_REQUEST_BUDGET_REASON"] = reason


def build_env() -> tuple[dict[str, str], dict[str, Any]]:
    env: dict[str, str] = {
        "HARIZON_API_STACK_POLICY_VERSION": POLICY_VERSION,
        "HARIZON_ACTIVE_API_STACK": "odds_api_io,sstats,bzzoiro,clubelo,football_data,thesportsdb,football_data_co_uk,weatherapi,open_meteo,wikidata,allsportsapi",
        "HARIZON_FROZEN_API_STACK": "sportlogic,futrixmetrics,oddspapi,sportsbook_api,freeapilivefootball,sportapi,oddsfeed,highlightly,api_football",
        "PROVIDER_CONTEXT_SOURCES_DO_NOT_CONFIRM_PRICE": "true",
        "PUBLISH_ALLOW_B_TIER": "true",
        "PUBLISH_COVERAGE_TIER_MODE": "hybrid",
        "CONTROLLED_FALLBACK_TELEGRAM_ALLOW_TIER_B": "true",
        "PROVIDER_PRICE_CONFIRMATION_MODE": "bookmaker_depth_first",
        "PUBLISH_MIN_PRICE_CONFIRMATIONS": os.getenv("PUBLISH_MIN_PRICE_CONFIRMATIONS") or "2",
        "CONTROLLED_FALLBACK_MIN_PRICE_CONFIRMATIONS": os.getenv("CONTROLLED_FALLBACK_MIN_PRICE_CONFIRMATIONS") or "2",
        "CONTROLLED_FALLBACK_REQUIRE_2_BOOKS_FOR_TELEGRAM": "false",
        "CONTROLLED_FALLBACK_REQUIRE_2_ODDS_SOURCES_FOR_TELEGRAM": "false",
        "CONTROLLED_FALLBACK_REQUIRE_2_CONTEXT_SOURCES_FOR_TELEGRAM": "false",
        "CONTROLLED_FALLBACK_MIN_CONTEXT_SOURCES": os.getenv("CONTROLLED_FALLBACK_MIN_CONTEXT_SOURCES") or "2",
        "CONTROLLED_FALLBACK_TELEGRAM_MIN_QUALITY": os.getenv("CONTROLLED_FALLBACK_TELEGRAM_MIN_QUALITY") or "70",
        "TELEGRAM_MAIN_PICK_PRICE_CONFIRMATION_MODE": "bookmaker_depth_first",
        "TELEGRAM_MAIN_PICK_MIN_PRICE_CONFIRMATIONS": os.getenv("TELEGRAM_MAIN_PICK_MIN_PRICE_CONFIRMATIONS") or "2",
        "MIN_BOOKS_FOR_CONSENSUS": "2",
        "MIN_BOOKS_PUBLISH": "2",
        "MARKET_DERIVED_MIN_BOOKS": "2",
        "MARKET_DERIVED_MIN_SOURCES": "1",
        "NEWS_INJURY_SHORTLIST_ENABLED": "true",
        "NEWS_INJURY_SHORTLIST_ONLY": "true",
        "NEWS_INJURY_SHORTLIST_LIMIT": os.getenv("NEWS_INJURY_SHORTLIST_LIMIT") or "8",
        "NEWS_GLOBAL_CONTEXT_ENABLED": "false",
        "NEWS_CONTEXT_NOT_EVERY_RUN": "true",
    }

    # Core line source.
    _put(env, "ENABLE_ODDS_API_IO", "true" if _present("ODDS_API_IO_KEY") else "false")
    _put(env, "ODDS_API_IO_ENABLED", "true" if _present("ODDS_API_IO_KEY") else "false")
    _put(env, "ODDS_API_IO_BOOKMAKERS", "Bet365,Unibet,Betfair Exchange,Sbobet")
    _put(env, "ODDS_API_IO_BOOKMAKERS_ACCOUNT1", "Bet365,Unibet")
    _put(env, "ODDS_API_IO_BOOKMAKERS_ACCOUNT2", "Betfair Exchange,Sbobet")
    _put(env, "TARGET_BOOKMAKERS", "Bet365,Unibet,Betfair Exchange,Sbobet")
    _put(env, "CONSENSUS_BOOKMAKERS", "Bet365,Unibet,Betfair Exchange,Sbobet")

    # Core context sources.
    _put(env, "ENABLE_SSTATS", "true" if _present("SSTATS_API_KEY") else "false")
    _put(env, "ENABLE_SSTATS_CONTEXT", "true" if _present("SSTATS_API_KEY") else "false")
    _put(env, "SSTATS_ENABLED", "true" if _present("SSTATS_API_KEY") else "false")
    _put(env, "SSTATS_CONTEXT_MATCH_LIMIT", os.getenv("SSTATS_CONTEXT_MATCH_LIMIT") or "120")
    _put(env, "SSTATS_HISTORICAL_ODDS_AS_LINES", "false")
    _put(env, "SSTATS_ROLLING_METRICS_ENABLED", "true")

    _put(env, "ENABLE_BZZOIRO", "true" if _present("BZZOIRO_API_KEY") else "false")
    _put(env, "ENABLE_BZZOIRO_CONTEXT", "true" if _present("BZZOIRO_API_KEY") else "false")
    _put(env, "BZZOIRO_ENABLED", "true" if _present("BZZOIRO_API_KEY") else "false")
    _put(env, "BZZOIRO_CONTEXT_MATCH_LIMIT", os.getenv("BZZOIRO_CONTEXT_MATCH_LIMIT") or "96")
    _put(env, "BZZOIRO_CURRENT_ODDS_AS_SECONDARY_SOURCE", "true")

    _put(env, "CLUBELO_ENABLED", "true")
    _put(env, "ENABLE_CLUBELO", "true")
    _put(env, "CLUBELO_PER_RUN_MAX", os.getenv("CLUBELO_PER_RUN_MAX") or "3")
    _put(env, "CLUBELO_CONTEXT_MATCH_LIMIT", os.getenv("CLUBELO_CONTEXT_MATCH_LIMIT") or "24")

    _put(env, "ENABLE_FOOTBALL_DATA_CONTEXT", "true" if _present("FOOTBALL_DATA_API_KEY", "FOOTBALL_DATA_KEY") else "false")
    _put(env, "FOOTBALL_DATA_ENABLED", "true" if _present("FOOTBALL_DATA_API_KEY", "FOOTBALL_DATA_KEY") else "false")
    _put(env, "FOOTBALL_DATA_CONTEXT_MATCH_LIMIT", os.getenv("FOOTBALL_DATA_CONTEXT_MATCH_LIMIT") or "120")
    _put(env, "FOOTBALL_DATA_CACHE_TTL_MINUTES", os.getenv("FOOTBALL_DATA_CACHE_TTL_MINUTES") or "720")

    _put(env, "ENABLE_THESPORTSDB_CONTEXT", "true")
    _put(env, "THESPORTSDB_CONTEXT_ENABLED", "true")
    _put(env, "THESPORTSDB_ENABLED", "true")
    _put(env, "THESPORTSDB_CONTEXT_MATCH_LIMIT", os.getenv("THESPORTSDB_CONTEXT_MATCH_LIMIT") or "120")
    if not _present("THESPORTSDB_API_KEY"):
        _put(env, "THESPORTSDB_API_KEY", "123")

    _put(env, "FOOTBALL_DATA_CO_UK_ENABLED", "true")
    _put(env, "FOOTBALL_DATA_CO_UK_CACHE_ONLY", "true")
    _put(env, "FOOTBALL_DATA_CO_UK_LIVE_HTTP_ENABLED", "false")
    _put(env, "WIKIDATA_ENABLED", "true")
    _put(env, "WIKIDATA_ALIAS_CACHE_ONLY", "true")
    _put(env, "WIKIDATA_LIVE_SPARQL_ENABLED", "false")
    _put(env, "WIKIDATA_MAX_REQUESTS_PER_DAY", "0")
    _put(env, "WIKIDATA_SPARQL_MAX_REQUESTS_PER_DAY", "0")

    # Weather: WeatherAPI first, Open-Meteo free fallback, OpenWeatherMap only emergency fallback.
    _put(env, "WEATHER_CONTEXT_ENABLED", "true")
    _put(env, "ENABLE_WEATHERAPI", "true" if _present("WEATHERAPI_KEY", "WEATHER_API_KEY", "WEATHERAPI_API_KEY") else "false")
    _put(env, "WEATHERAPI_ENABLED", "true" if _present("WEATHERAPI_KEY", "WEATHER_API_KEY", "WEATHERAPI_API_KEY") else "false")
    _put(env, "OPEN_METEO_ENABLED", "true")
    _put(env, "ENABLE_OPEN_METEO", "true")
    _put(env, "WEATHER_PRIMARY_PROVIDERS", "weatherapi,open_meteo")
    _put(env, "WEATHER_OPENWEATHERMAP_FALLBACK_ENABLED", "false")
    _put_limit(env, "OPENWEATHERMAP", 0)

    # Controlled fixture/alias rescue only.
    _put(env, "ENABLE_ALLSPORTSAPI", "true" if _present("ALLSPORTSAPI_API_KEY") else "false")
    _put(env, "ALLSPORTSAPI_ENABLED", "true" if _present("ALLSPORTSAPI_API_KEY") else "false")
    _put(env, "ALLSPORTSAPI_ONLY_IF_PRIMARY_ODDS_EMPTY", "true")
    _put(env, "ALLSPORTSAPI_FIXTURE_RESCUE_ONLY", "true")
    _put(env, "ALLSPORTSAPI_CONTEXT_MATCH_LIMIT", os.getenv("ALLSPORTSAPI_CONTEXT_MATCH_LIMIT") or "24")
    _put(env, "ALLSPORTSAPI_MATCH_LIMIT", os.getenv("ALLSPORTSAPI_MATCH_LIMIT") or "32")

    # Freeze low-yield / broken / duplicate providers in normal runtime.
    frozen = {
        "SPORTLOGIC": "frozen_zero_matched_zero_ctx_until_parser_mapping_fixed",
        "FUTRIXMETRICS": "frozen_low_recent_yield_zero_context",
        "ODDSPAPI": "frozen_missing_key_or_unverified_schema",
        "API_FOOTBALL": "frozen_api_sports_overlap_daily_limited",
        "RAPIDAPI_SPORTSBOOK": "frozen_50_day_too_small_for_every_run",
        "RAPIDAPI_FREE_FOOTBALL": "frozen_100_month_too_small_for_every_run",
        "SPORTAPI": "frozen_api_sports_overlap",
        "FREEAPILIVEFOOTBALL": "frozen_100_month_too_small_for_every_run",
        "RAPIDAPI_ODDS_FEED": "frozen_monthly_limited_unverified_schema",
        "ODDSFEED": "frozen_monthly_limited_unverified_schema",
        "HIGHLIGHTLY": "frozen_probe_only_until_parser_verified",
        "METEOSTAT": "frozen_weatherapi_openmeteo_first",
        "SHARPAPI": "frozen_not_core_prediction",
        "BOOKIES_API": "removed_from_project",
    }
    for prefix, reason in frozen.items():
        _disable(env, prefix, reason)
    env["SPORTLOGIC_CONTROLLED_ODDS_ENABLED"] = "false"
    env["SPORTLOGIC_ONLY_IF_PRIMARY_ODDS_EMPTY"] = "false"

    # News: keep one primary + one fallback for shortlist only; disable duplicate global scans.
    env.update({
        "NEWSAPI_PER_RUN_MAX": "0",
        "NEWSAPI_MAX_HTTP_REQUESTS_PER_RUN": "0",
        "CURRENTS_NEWS_PER_RUN_MAX": "0",
        "CURRENTS_NEWS_MAX_HTTP_REQUESTS_PER_RUN": "0",
        "GNEWS_PER_RUN_MAX": "0",
        "GNEWS_MAX_HTTP_REQUESTS_PER_RUN": "0",
        "GNEWS_CONTEXT_MATCH_LIMIT": "0",
        "NEWSDATA_SHORTLIST_ONLY": "true",
        "GUARDIAN_SHORTLIST_ONLY": "true",
        "NEWSDATA_PER_RUN_MAX": os.getenv("NEWSDATA_PER_RUN_MAX") or "0",
        "GUARDIAN_PER_RUN_MAX": os.getenv("GUARDIAN_PER_RUN_MAX") or "0",
    })

    audit = {
        "version": POLICY_VERSION,
        "core_every_run": [
            "odds_api_io",
            "sstats",
            "bzzoiro",
            "clubelo",
            "football_data",
            "thesportsdb",
            "football_data_co_uk_cache",
            "weatherapi",
            "open_meteo",
            "wikidata_alias_cache",
        ],
        "controlled_fallback": ["allsportsapi_fixture_alias_only", "openweathermap_emergency_only", "newsdata_guardian_shortlist_only"],
        "frozen": frozen,
        "price_confirmation_mode": "bookmaker_depth_first_2plus_book_lines",
        "context_confirmation_min": 2,
        "quality_min_for_controlled_telegram": env["CONTROLLED_FALLBACK_TELEGRAM_MIN_QUALITY"],
    }
    return env, audit


def write_env(env: dict[str, str]) -> None:
    for key, value in env.items():
        os.environ[key] = value
    if not GITHUB_ENV:
        for key in sorted(env):
            print(f"{key}={env[key]}")
        return
    with open(GITHUB_ENV, "a", encoding="utf-8") as fh:
        for key in sorted(env):
            fh.write(f"{key}={env[key]}\n")


def main() -> int:
    env, audit = build_env()
    audit["created_at_utc"] = datetime.now(timezone.utc).isoformat()
    audit["env_written_count"] = len(env)
    EXPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    EXPORT_PATH.write_text(json.dumps(audit, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    write_env(env)
    print(json.dumps(audit, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
