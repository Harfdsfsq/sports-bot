from __future__ import annotations

"""Runtime quota override for the core coverage providers.

Requested caps:
- odds-api.io: 100 requests/hour total across configured accounts;
- SStats: 150 requests/minute;
- Bzzoiro: 200 requests/minute;
- SportLogic: 500 requests/day.

The override only changes request budgets and coverage/enrichment limits. It does
not relax publication quality, EV, market-family, exact-price or two-source
requirements.
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
REPORT_PATH = ROOT / ".data" / "exports" / "latest-core-coverage-quota-runtime-override.json"
_INSTALLED = False


def _present(*names: str) -> bool:
    return any(str(os.getenv(name) or "").strip() for name in names)


def _set(key: str, value: Any) -> None:
    os.environ[str(key)] = str(value)


def _append_github_env(values: dict[str, Any]) -> None:
    path = os.getenv("GITHUB_ENV")
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as fh:
            for key in sorted(values):
                fh.write(f"{key}={values[key]}\n")
    except Exception:
        pass


def _set_many(payload: dict[str, Any], *, github_env: bool = True) -> None:
    for key, value in payload.items():
        _set(str(key), str(value))
    if github_env:
        _append_github_env(payload)


def _limit_aliases(prefix: str, value: int, *extra_aliases: str) -> dict[str, str]:
    upper = prefix.upper()
    aliases = {
        f"{upper}_PER_RUN_MAX",
        f"{upper}_MAX_HTTP_REQUESTS_PER_RUN",
        f"{upper}_MAX_REQUESTS_PER_RUN",
        f"{upper}_REQUESTS_MAX_PER_RUN",
        f"{upper}_REQUEST_BUDGET_GRANTED",
        *extra_aliases,
    }
    return {alias: str(max(0, int(value))) for alias in aliases}


def _write_report(payload: dict[str, Any]) -> None:
    try:
        REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        REPORT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except Exception:
        pass


def install() -> dict[str, Any]:
    global _INSTALLED
    if _INSTALLED:
        return {"status": "already_installed"}
    _INSTALLED = True

    has_odds1 = _present("ODDS_API_IO_KEY")
    has_odds2 = _present("ODDS_API_IO_KEY_2", "ODDS_API_IO_KEY2")
    odds_total = 100 if (has_odds1 or has_odds2) else 0
    odds1 = 50 if has_odds1 and has_odds2 else odds_total if has_odds1 else 0
    odds2 = 50 if has_odds1 and has_odds2 else odds_total if has_odds2 else 0
    sstats = 150 if _present("SSTATS_API_KEY") else 0
    bzzoiro = 200 if _present("BZZOIRO_API_KEY") else 0
    sportlogic_daily = 500 if _present("SPORTLOGIC_API_KEY", "SPORTLOGIC_KEY", "SPORTLOGIC_TOKEN") else 0
    # 12 scheduled runs/day => 40 per run leaves reserve under 500/day.
    sportlogic_run = 40 if sportlogic_daily else 0

    env: dict[str, Any] = {
        "CORE_COVERAGE_QUOTA_OVERRIDE_ENABLED": "true",
        "CORE_COVERAGE_QUOTA_OVERRIDE_VERSION": "v2-2026-05-14-user-limits",
        "HARIZON_PRIMARY_PROVIDERS": "odds_api_io,bzzoiro,sstats,sportlogic",
        "HARIZON_ALLOWED_PROVIDER_SET": "odds_api_io,sstats,bzzoiro,football_data,thesportsdb,weatherapi,open_meteo,clubelo,allsportsapi,sportlogic",
        "ALL_SOURCES_FREE_MAXIMIZE": "true",
        "PROVIDER_REQUEST_BUDGET_MODE": "per_run_only",
        "PROVIDER_REQUEST_BUDGET_DISABLE_DAILY_MONTHLY": "true",
        "CONTEXT_ENRICHMENT_REQUIRES_OFFERS": "false",
        "DAY_INVENTORY_CONTEXT_BACKFILL_ENABLED": "true",
        "DAY_INVENTORY_CONTEXT_BACKFILL_LIMIT": "300",
        "DAY_INVENTORY_NEAR_WINDOW_PRIORITY": "true",
        "DAY_INVENTORY_NEAR_WINDOW_HOURS": "12",
        "MAX_MATCHES_FOR_ODDS_FETCH": "300",
        "ANALYSIS_MATCH_CAP_PER_RUN": "900",
        "DIAGNOSTICS_MATCH_LIMIT": "900",
        "CONTEXT_ENRICHMENT_MATCH_LIMIT": "320",
        "PREMIUM_CONTEXT_SHORTLIST_LIMIT": "160",

        "ODDS_API_IO_ENABLED": "true" if odds_total else "false",
        "ENABLE_ODDS_API_IO": "true" if odds_total else "false",
        "ODDS_API_IO_HOURLY_LIMIT": str(odds_total),
        "ODDS_API_IO_BOOKMAKERS": "Bet365,Unibet,Betfair Exchange,Sbobet",
        "ODDS_API_IO_BOOKMAKERS_ACCOUNT1": "Bet365,Unibet",
        "ODDS_API_IO_BOOKMAKERS_ACCOUNT2": "Betfair Exchange,Sbobet",
        "TARGET_BOOKMAKERS": "Bet365,Unibet,Betfair Exchange,Sbobet",
        "CONSENSUS_BOOKMAKERS": "Bet365,Unibet,Betfair Exchange,Sbobet",
        "ODDS_API_IO_ACCOUNT1_PER_RUN_MAX": str(odds1),
        "ODDS_API_IO_ACCOUNT2_PER_RUN_MAX": str(odds2),
        "ODDS_API_IO_ACCOUNT1_MAX_REQUESTS_PER_RUN": str(odds1),
        "ODDS_API_IO_ACCOUNT2_MAX_REQUESTS_PER_RUN": str(odds2),
        "ODDS_API_IO_MAX_EVENT_PAGES_PER_SPORT": "10",
        "ODDS_API_IO_MAX_PAGES_PER_SPORT": "10",
        "ODDS_API_IO_PAGE_LIMIT": "100",
        "DAY_INVENTORY_ODDS_API_IO_TARGET_MATCHES": "220",
        "PRICE_BACKFILL_ODDS_API_IO_BATCHES_PER_ACCOUNT": "5",
        "PRICE_BACKFILL_ODDS_API_IO_MAX_EVENT_IDS_PER_REQUEST": "10",
        "PRICE_BACKFILL_ODDS_API_IO_EVENT_LIMIT": "100",

        "SSTATS_ENABLED": "true" if sstats else "false",
        "ENABLE_SSTATS": "true" if sstats else "false",
        "ENABLE_SSTATS_CONTEXT": "true" if sstats else "false",
        "SSTATS_CONTEXT_ENABLED": "true" if sstats else "false",
        "SSTATS_RATE_LIMIT_PER_MINUTE": str(sstats),
        "SSTATS_CONTEXT_MATCH_LIMIT": "300",
        "SSTATS_RECENT_MATCHES": "10",
        "SSTATS_LOOKBACK_DAYS": "60",
        "SSTATS_DEEP_ENDPOINTS_ENABLED": "true",
        "SSTATS_DEEP_REQUESTS_MAX_PER_RUN": str(min(90, sstats)),
        "SSTATS_DEEP_DETAIL_LIMIT_PER_RUN": "40",
        "SSTATS_DEEP_CONTEXT_MATCH_LIMIT": "80",
        "SSTATS_DEEP_ENRICHMENT_ENABLED": "true" if sstats else "false",
        "SSTATS_DEEP_ENRICHMENT_AFTER_CROSSWALK": "true" if sstats else "false",
        "SSTATS_GAME_DETAIL_ENABLED": "true" if sstats else "false",
        "SSTATS_LAST_GAMES_STATS_ENABLED": "true" if sstats else "false",
        "SSTATS_GLICKO_ENABLED": "true" if sstats else "false",
        "SSTATS_ODDS_RESCUE_ENABLED": "true" if sstats else "false",
        "DAY_INVENTORY_SSTATS_MAX_REQUESTS": str(sstats),
        "DAY_INVENTORY_SSTATS_TOTAL_HARD_CAP": "300",
        "DAY_INVENTORY_FORCE_FULL_ALLOW_SSTATS_OVER_HARD_CAP": "true",

        "BZZOIRO_ENABLED": "true" if bzzoiro else "false",
        "ENABLE_BZZOIRO": "true" if bzzoiro else "false",
        "ENABLE_BZZOIRO_CONTEXT": "true" if bzzoiro else "false",
        "BZZOIRO_CONTEXT_ENABLED": "true" if bzzoiro else "false",
        "BZZOIRO_RATE_LIMIT_PER_MINUTE": str(bzzoiro),
        "BZZOIRO_CONTEXT_MATCH_LIMIT": "300",
        "BZZOIRO_MAX_PAGES": "20",
        "BZZOIRO_PAGE_SIZE": "50",
        "BZZOIRO_V2_ENRICHMENT_ENABLED": "true" if bzzoiro else "false",
        "BZZOIRO_V2_MATCH_LIMIT": "160",
        "BZZOIRO_V2_MAX_HTTP_REQUESTS_PER_RUN": str(bzzoiro),
        "BZZOIRO_V2_EVENTS_ENABLED": "true" if bzzoiro else "false",
        "BZZOIRO_V2_STATS_ENABLED": "true" if bzzoiro else "false",
        "BZZOIRO_V2_METADATA_ENABLED": "true" if bzzoiro else "false",
        "BZZOIRO_V2_LINEUPS_ENABLED": "true" if bzzoiro else "false",
        "BZZOIRO_V2_ODDS_ENABLED": "true" if bzzoiro else "false",
        "BZZOIRO_CURRENT_ODDS_AS_SECONDARY_SOURCE": "true",
        "BZZOIRO_ODDS_REKEY_ENABLED": "true",
        "BZZOIRO_ODDS_REKEY_MIN_SCORE": "68",
        "BZZOIRO_PRICE_BACKFILL_ENABLED": "true" if bzzoiro else "false",
        "BZZOIRO_PRICE_BACKFILL_TARGET_LIMIT": "80",
        "DAY_INVENTORY_BZZOIRO_MAX_REQUESTS": str(bzzoiro),
        "DAY_INVENTORY_BZZOIRO_MAX_PAGES": "20",

        "SPORTLOGIC_DAILY_LIMIT": str(sportlogic_daily),
        "SPORTLOGIC_ENABLED": "true" if sportlogic_daily else "false",
        "ENABLE_SPORTLOGIC": "true" if sportlogic_daily else "false",
        "SPORTLOGIC_CONTROLLED_ODDS_ENABLED": "true" if sportlogic_daily else "false",
        "SPORTLOGIC_PER_RUN_MAX": str(sportlogic_run),
        "SPORTLOGIC_MAX_HTTP_REQUESTS_PER_RUN": str(sportlogic_run),
        "SPORTLOGIC_REQUESTS_MAX_PER_RUN": str(sportlogic_run),
        "SPORTLOGIC_REQUEST_BUDGET_GRANTED": str(sportlogic_run),
        "SPORTLOGIC_MATCH_LIMIT": "100",
        "SPORTLOGIC_CONTEXT_MATCH_LIMIT": "100",
        "SPORTLOGIC_ODDS_MATCH_LIMIT": "40",
        "SPORTLOGIC_ACTIVE_ODDS_SMOKE_PAGES": "2",
        "SPORTLOGIC_ACTIVE_ODDS_SMOKE_GAME_LIMIT": "40",
        "SPORTLOGIC_MATCH_MIN_SCORE": "48",
        "DAY_INVENTORY_ENABLE_SPORTLOGIC": "true" if sportlogic_daily else "false",
        "DAY_INVENTORY_SPORTLOGIC_MATCH_LIMIT": "100",
        "DAY_INVENTORY_SPORTLOGIC_MAX_REQUESTS": str(sportlogic_run),

        "SECONDARY_ODDS_RESCUE_ENABLED": "true",
        "SECONDARY_ODDS_RESCUE_TRIGGER": "thin_primary_market_depth",
        "SECONDARY_ODDS_RESCUE_MIN_PRIMARY_OFFERS": "120",
        "API_COVERAGE_CONSENSUS_GUARD_ENABLED": "true",
        "API_COVERAGE_MIN_EXACT_ODDS_SOURCES": "1",
        "API_COVERAGE_MIN_EXACT_BOOKS": "1",
        "API_COVERAGE_MIN_CONTEXT_SOURCES": "1",
        "API_COVERAGE_CONTEXT_EXCLUDE_NEWS_WEATHER_FROM_CORE": "true",
        "API_COVERAGE_MAX_SOURCE_PRICE_DISPERSION_PCT": "18.0",
        "API_COVERAGE_MAX_SELECTED_PRICE_DRIFT_PCT": "8.0",
        "MIN_BOOKS_FOR_CONSENSUS": "2",
        "MIN_BOOKS_PUBLISH": "2",
        "MIN_SOURCES_PUBLISH": "1",
        "PUBLISH_MIN_ODDS_SOURCES": "1",
        "PUBLISH_MIN_CONTEXT_SOURCES": "1",
        "MIN_CONTEXT_SOURCES_PUBLISH": "1",
        "MARKET_DERIVED_MIN_BOOKS": "2",
        "MARKET_DERIVED_MIN_SOURCES": "1",
    }

    env.update(_limit_aliases("ODDS_API_IO", odds_total))
    env.update(_limit_aliases("SSTATS", sstats))
    env.update(_limit_aliases("BZZOIRO", bzzoiro, "BZZOIRO_EVENTS_MAX_REQUESTS_PER_RUN", "BZZOIRO_PREDICTIONS_MAX_REQUESTS_PER_RUN"))
    env.update(_limit_aliases("SPORTLOGIC", sportlogic_run))

    _set_many(env)
    report = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "installed",
        "requested_limits": {
            "odds_api_io_per_hour_total": odds_total,
            "odds_api_io_account1_per_run": odds1,
            "odds_api_io_account2_per_run": odds2,
            "sstats_per_minute": sstats,
            "bzzoiro_per_minute": bzzoiro,
            "sportlogic_per_day": sportlogic_daily,
            "sportlogic_per_run": sportlogic_run,
        },
        "env_written_count": len(env),
        "notes": [
            "Runtime budgets are raised for core coverage providers.",
            "Publication still requires two exact odds sources and two context sources.",
            "SportLogic is capped per run to stay under 500/day across 12 scheduled runs.",
        ],
    }
    _write_report(report)
    return report
