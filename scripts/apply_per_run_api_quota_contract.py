from __future__ import annotations

"""Apply the HARIZON per-run API quota contract.

This is the final env layer after provider quota governor/request budget.  The
repo has accumulated several aliases for the same limit
(`*_PER_RUN_MAX`, `*_MAX_HTTP_REQUESTS_PER_RUN`, `*_MAX_REQUESTS_PER_RUN`,
`*_REQUESTS_MAX_PER_RUN`).  Providers read different aliases, so this script
writes all known aliases from one per-run source of truth.

Policy goals:
- 00:00-02:59 local: inventory-first, collect the full day.
- morning: backfill lines/context from day inventory.
- live: near-window odds refresh + context shortlist.
- channel publication: normally 2 odds sources and >=2 bookmakers.
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


def _truthy(value: object) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on", "force"}


def _present(*names: str) -> bool:
    return any(str(os.getenv(name) or "").strip() for name in names)


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


def _daily_to_per_run(daily: int, runs_per_day: int = 12, safety: float = 0.78) -> int:
    return max(0, int((daily * safety) // max(1, runs_per_day)))


def _monthly_to_per_run(monthly: int, runs_per_day: int = 12, safety: float = 0.78) -> int:
    return max(0, int((monthly * safety) // max(1, runs_per_day * 30)))


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


def _provider_contract(phase: str) -> tuple[dict[str, str], dict[str, Any]]:
    runs_per_day = int(float(os.getenv("PROVIDER_QUOTA_RUNS_PER_DAY") or 12))
    safety = float(os.getenv("PROVIDER_QUOTA_SAFETY_FACTOR") or 0.78)

    # Conservative free limits from api_free_limits_ru.*.  Values are grants per
    # run, not hard free limits.  They are intentionally below public limits and
    # adapted by phase.
    if phase == "full_inventory":
        odds_total = 160
        odds_account = 80
        max_matches_for_odds = 900
        analysis_cap = 900
        context_limit = 120
        premium_context = 48
        weather_limit = 8
        bzzoiro = 12
        sstats = 20
        football_data = 8
        thesportsdb = 18
        sportlogic = 32
        allsports = 18
    elif phase == "morning_backfill":
        odds_total = 140
        odds_account = 70
        max_matches_for_odds = 650
        analysis_cap = 650
        context_limit = 260
        premium_context = 96
        weather_limit = 16
        bzzoiro = 18
        sstats = 30
        football_data = 8
        thesportsdb = 16
        sportlogic = 28
        allsports = 18
    else:
        odds_total = 120
        odds_account = 60
        max_matches_for_odds = 520
        analysis_cap = 520
        context_limit = 240
        premium_context = 96
        weather_limit = 12
        bzzoiro = 24
        sstats = 36
        football_data = 6
        thesportsdb = 10
        sportlogic = 24
        allsports = 12

    # Daily/monthly limited sources.  Use per-run budget only when useful and
    # keep monthly-limited APIs as probe/rescue, never mass scan.
    api_football = min(6, _daily_to_per_run(100, runs_per_day, safety)) if _present("API_FOOTBALL_KEY", "API_FOOTBALL_API_KEY") else 0
    highlightly = min(6, _daily_to_per_run(100, runs_per_day, safety)) if _present("HIGHLIGHTLY_API_KEY") else 0
    sportsbook = min(3, _daily_to_per_run(50, runs_per_day, safety)) if _present("SPORTSBOOK_API_KEY", "SPORTSBOOK_KEY", "RAPIDAPI_KEY") else 0
    oddspapi = 1 if _present("ODDSPAPI_API_KEY", "ODDSPAPI_KEY", "ODDS_PAPI_API_KEY") and phase in {"morning_backfill", "live_refresh"} else 0
    oddsfeed = 1 if _present("ODDS_FEED_RAPIDAPI_KEY", "RAPIDAPI_KEY") and phase == "live_refresh" else 0
    free_live = 0  # 100/month: too small for scheduled runs.
    meteostat = 0  # 500/month: keep disabled; Open-Meteo/WeatherAPI first.

    env: dict[str, str] = {
        "HARIZON_API_QUOTA_CONTRACT_VERSION": "v1-per-run-free-limits-2026-05-09",
        "HARIZON_RUN_PHASE_EFFECTIVE": phase,
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
        "CONTEXT_ENRICHMENT_REQUIRES_OFFERS": "true",
        "DAY_INVENTORY_USE_FOR_RUN": "true",
        "DAY_INVENTORY_NEAR_WINDOW_PRIORITY": "true",
        "DAY_INVENTORY_NEAR_WINDOW_HOURS": "12",
        "DAY_INVENTORY_BOOTSTRAP_PROVIDER": "odds_api_io",
        "MATCH_BOOTSTRAP_PROVIDER": "odds_api_io",
        "DAY_INVENTORY_FORCE_PROVIDER_MERGE": "true" if phase == "full_inventory" else "false",
        "DAY_INVENTORY_COVERAGE_MAX_REBUILD": "true" if phase == "full_inventory" else "false",
        "SECONDARY_ODDS_RESCUE_ENABLED": "true",
        "SECONDARY_ODDS_RESCUE_TRIGGER": "thin_primary_market_depth" if phase != "full_inventory" else "odds_api_io_empty_or_thin",
        "SECONDARY_ODDS_RESCUE_MIN_PRIMARY_OFFERS": "40",
        "SECONDARY_ODDS_RESCUE_NEAR_WINDOW_HOURS": "12",
        # Publication/market-depth contract.
        "PROVIDER_CONTEXT_SOURCES_DO_NOT_CONFIRM_PRICE": "true",
        "MIN_BOOKS_FOR_CONSENSUS": "2",
        "MIN_BOOKS_PUBLISH": "2",
        "MIN_SOURCES_PUBLISH": "2",
        "MARKET_DERIVED_MIN_BOOKS": "2",
        "MARKET_DERIVED_MIN_SOURCES": "2",
        "MARKET_DERIVED_CONSENSUS_RELIEF_MIN_BOOKS": "2",
        "MARKET_DERIVED_CONSENSUS_RELIEF_MIN_SOURCES": "2",
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
        # odds-api.io account routing.
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

    # Core context/fixtures.
    env.update({"ENABLE_SSTATS_CONTEXT": "true" if _present("SSTATS_API_KEY") else "false", "SSTATS_ENABLED": "true" if _present("SSTATS_API_KEY") else "false", "SSTATS_RECENT_MATCHES": "10", "SSTATS_LOOKBACK_DAYS": "45", "SSTATS_CONTEXT_MATCH_LIMIT": "72"})
    _put_limit(env, "SSTATS", sstats if _present("SSTATS_API_KEY") else 0)
    env.update({"ENABLE_BZZOIRO_CONTEXT": "true" if _present("BZZOIRO_API_KEY") else "false", "BZZOIRO_ENABLED": "true" if _present("BZZOIRO_API_KEY") else "false", "BZZOIRO_CONTEXT_MATCH_LIMIT": "72", "BZZOIRO_MAX_PAGES": "6", "BZZOIRO_PAGE_SIZE": "10"})
    _put_limit(env, "BZZOIRO", bzzoiro if _present("BZZOIRO_API_KEY") else 0, "BZZOIRO_EVENTS_MAX_REQUESTS_PER_RUN", "BZZOIRO_PREDICTIONS_MAX_REQUESTS_PER_RUN")
    env.update({"ENABLE_FOOTBALL_DATA_CONTEXT": "true" if _present("FOOTBALL_DATA_API_KEY", "FOOTBALL_DATA_KEY") else "false", "FOOTBALL_DATA_ENABLED": "true" if _present("FOOTBALL_DATA_API_KEY", "FOOTBALL_DATA_KEY") else "false", "FOOTBALL_DATA_CONTEXT_MATCH_LIMIT": "72", "FOOTBALL_DATA_CACHE_TTL_MINUTES": "720"})
    _put_limit(env, "FOOTBALL_DATA", football_data if _present("FOOTBALL_DATA_API_KEY", "FOOTBALL_DATA_KEY") else 0)
    env.update({"ENABLE_THESPORTSDB_CONTEXT": "true", "THESPORTSDB_CONTEXT_ENABLED": "true", "THESPORTSDB_API_KEY": os.getenv("THESPORTSDB_API_KEY") or "123", "THESPORTSDB_CONTEXT_MATCH_LIMIT": "96"})
    _put_limit(env, "THESPORTSDB", thesportsdb)

    # Secondary odds/rescue sources.
    env.update({"ENABLE_ALLSPORTSAPI": "true" if _present("ALLSPORTSAPI_API_KEY", "ALLSPORTSAPI_KEY") else "false", "ALLSPORTSAPI_ENABLED": "true" if _present("ALLSPORTSAPI_API_KEY", "ALLSPORTSAPI_KEY") else "false", "ALLSPORTSAPI_MATCH_LIMIT": "48", "ALLSPORTSAPI_ONLY_IF_PRIMARY_ODDS_EMPTY": "false" if phase != "full_inventory" else "true"})
    _put_limit(env, "ALLSPORTSAPI", allsports if _present("ALLSPORTSAPI_API_KEY", "ALLSPORTSAPI_KEY") else 0)
    env.update({"ENABLE_SPORTLOGIC": "true" if _present("SPORTLOGIC_API_KEY", "SPORTLOGIC_KEY", "SPORTLOGIC_TOKEN") else "false", "SPORTLOGIC_ENABLED": "true" if _present("SPORTLOGIC_API_KEY", "SPORTLOGIC_KEY", "SPORTLOGIC_TOKEN") else "false", "SPORTLOGIC_CONTROLLED_ODDS_ENABLED": "true", "SPORTLOGIC_ODDS_MATCH_LIMIT": "24", "SPORTLOGIC_CONTEXT_MATCH_LIMIT": "60", "SPORTLOGIC_MATCH_LIMIT": "60", "SPORTLOGIC_PER_PAGE": "50", "SPORTLOGIC_MIN_SECONDS_BETWEEN_REQUESTS": "7", "SPORTLOGIC_ONLY_IF_PRIMARY_ODDS_EMPTY": "false" if phase != "full_inventory" else "true"})
    _put_limit(env, "SPORTLOGIC", sportlogic if _present("SPORTLOGIC_API_KEY", "SPORTLOGIC_KEY", "SPORTLOGIC_TOKEN") else 0)
    env.update({"ENABLE_ODDSPAPI": "true" if oddspapi else "false", "ODDSPAPI_ENABLED": "true" if oddspapi else "false", "ODDSPAPI_MATCH_LIMIT": "12", "ODDSPAPI_CONTEXT_MATCH_LIMIT": "0", "ODDSPAPI_ONLY_IF_PRIMARY_ODDS_EMPTY": "false"})
    _put_limit(env, "ODDSPAPI", oddspapi)

    # Daily/monthly limited football context/probes.
    env.update({"ENABLE_API_FOOTBALL": "true" if api_football else "false", "API_FOOTBALL_ENABLED": "true" if api_football else "false", "API_FOOTBALL_CONTEXT_MATCH_LIMIT": "12", "API_FOOTBALL_PREDICTIONS_LIMIT": "0"})
    _put_limit(env, "API_FOOTBALL", api_football)
    env.update({"HIGHLIGHTLY_CONTEXT_MATCH_LIMIT": "8"})
    _put_limit(env, "HIGHLIGHTLY", highlightly)
    _put_limit(env, "RAPIDAPI_SPORTSBOOK", sportsbook)
    _put_limit(env, "RAPIDAPI_ODDS_FEED", oddsfeed)
    _put_limit(env, "RAPIDAPI_FREE_FOOTBALL", free_live)

    # Weather/news/reference.
    env.update({"WEATHER_CONTEXT_ENABLED": "true", "WEATHER_CONTEXT_MATCH_LIMIT": str(weather_limit), "ENABLE_WEATHERAPI": "true" if _present("WEATHERAPI_KEY", "WEATHER_API_KEY", "WEATHERAPI_API_KEY") else "false", "WEATHERAPI_ENABLED": "true" if _present("WEATHERAPI_KEY", "WEATHER_API_KEY", "WEATHERAPI_API_KEY") else "false"})
    _put_limit(env, "WEATHERAPI", weather_limit if _present("WEATHERAPI_KEY", "WEATHER_API_KEY", "WEATHERAPI_API_KEY") else 0)
    _put_limit(env, "OPENWEATHERMAP", 4 if _present("OPENWEATHERMAP_API_KEY", "OPENWEATHER_API_KEY", "OPENWEATHERMAP_KEY") else 0)
    _put_limit(env, "OPEN_METEO", 80)
    # News rotation: off in full inventory, tiny per-run in live if keys exist.
    news_grant = 0 if phase == "full_inventory" else 2
    gnews_grant = 0 if phase == "full_inventory" else 2
    currents_grant = 0 if phase == "full_inventory" else 2
    newsdata_grant = 0 if phase == "full_inventory" else 2
    guardian_grant = 0 if phase == "full_inventory" else 2
    _put_limit(env, "NEWSAPI", news_grant if _present("NEWSAPI_KEY") else 0)
    _put_limit(env, "CURRENTS", currents_grant if _present("CURRENTS_API_KEY", "CURRENTS_KEY") else 0)
    _put_limit(env, "GNEWS", gnews_grant if _present("GNEWS_KEY") else 0)
    _put_limit(env, "NEWSDATA", newsdata_grant if _present("NEWSDATA_API_KEY") else 0)
    _put_limit(env, "GUARDIAN", guardian_grant if _present("GUARDIAN_API_KEY") else 0)
    _put_limit(env, "METEOSTAT", meteostat)

    # Known low-yield or cache-first sources.
    env.update({"ENABLE_FUTRIXMETRICS_CONTEXT": "false", "FUTRIXMETRICS_ENABLED": "false", "FUTRIXMETRICS_CONTEXT_MATCH_LIMIT": "0"})
    _put_limit(env, "FUTRIXMETRICS", 0)
    env.update({"CLUBELO_ENABLED": "true", "FOOTBALL_DATA_CO_UK_ENABLED": "true", "WIKIDATA_ENABLED": "true", "WIKIDATA_MAX_REQUESTS_PER_DAY": "8", "WIKIDATA_SPARQL_MAX_REQUESTS_PER_DAY": "2"})

    contract = {
        "phase": phase,
        "runs_per_day": runs_per_day,
        "safety_factor": safety,
        "free_limits_reference": "api_free_limits_ru.pdf/docx checked into project docs/upload",
        "per_run_grants": {
            "odds_api_io": odds_total if _present("ODDS_API_IO_KEY") else 0,
            "odds_api_io_account1": odds_account if _present("ODDS_API_IO_KEY") else 0,
            "odds_api_io_account2": odds_account if _present("ODDS_API_IO_KEY_2", "ODDS_API_IO_KEY2") else 0,
            "sstats": sstats if _present("SSTATS_API_KEY") else 0,
            "bzzoiro": bzzoiro if _present("BZZOIRO_API_KEY") else 0,
            "football_data": football_data if _present("FOOTBALL_DATA_API_KEY", "FOOTBALL_DATA_KEY") else 0,
            "thesportsdb": thesportsdb,
            "allsportsapi": allsports if _present("ALLSPORTSAPI_API_KEY", "ALLSPORTSAPI_KEY") else 0,
            "sportlogic": sportlogic if _present("SPORTLOGIC_API_KEY", "SPORTLOGIC_KEY", "SPORTLOGIC_TOKEN") else 0,
            "oddspapi": oddspapi,
            "api_football": api_football,
            "highlightly": highlightly,
            "weatherapi": weather_limit if _present("WEATHERAPI_KEY", "WEATHER_API_KEY", "WEATHERAPI_API_KEY") else 0,
            "openweathermap": 4 if _present("OPENWEATHERMAP_API_KEY", "OPENWEATHER_API_KEY", "OPENWEATHERMAP_KEY") else 0,
            "futrixmetrics": 0,
            "newsapi": news_grant if _present("NEWSAPI_KEY") else 0,
            "gnews": gnews_grant if _present("GNEWS_KEY") else 0,
            "newsdata": newsdata_grant if _present("NEWSDATA_API_KEY") else 0,
            "guardian": guardian_grant if _present("GUARDIAN_API_KEY") else 0,
        },
        "notes": [
            "Limits are per-run env grants. Providers must read one of the written aliases.",
            "Context providers do not count as price confirmation.",
            "FutrixMetrics is hard-disabled until mapping yields contexts.",
            "Oddspapi/OddsFeed are monthly-limited and kept as 1-call shortlist probes only.",
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
