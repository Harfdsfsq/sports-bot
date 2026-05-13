from __future__ import annotations

"""Runtime quota override for the core coverage providers.

The per-run contract is intentionally conservative in older layers, but the
production strategy now needs the three core providers to be used aggressively:
- odds_api_io: two accounts/bookmaker sets, up to 100 requests each per hour;
- sstats: rich context/deep endpoints, up to 150 requests per run/minute window;
- bzzoiro: broad context + v2 odds/stats/metadata, capped locally at 150 per run.

This module runs from usercustomize/sitecustomize before app settings are built.
It only changes request budgets and coverage limits; it does not relax publish
quality, EV, price integrity, market-family or two-source requirements.
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


def _set_many(payload: dict[str, Any]) -> None:
    for key, value in payload.items():
        _set(key, value)


def _put_limit(prefix: str, value: int, *extra_aliases: str) -> dict[str, str]:
    upper = prefix.upper()
    aliases = {
        f"{upper}_PER_RUN_MAX",
        f"{upper}_MAX_HTTP_REQUESTS_PER_RUN",
        f"{upper}_MAX_REQUESTS_PER_RUN",
        f"{upper}_REQUESTS_MAX_PER_RUN",
        f"{upper}_REQUEST_BUDGET_GRANTED",
        *extra_aliases,
    }
    payload = {alias: str(max(0, int(value))) for alias in aliases}
    _set_many(payload)
    return payload


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

    odds1 = 100 if _present("ODDS_API_IO_KEY") else 0
    odds2 = 100 if _present("ODDS_API_IO_KEY_2", "ODDS_API_IO_KEY2") else 0
    odds_total = odds1 + odds2
    sstats = 150 if _present("SSTATS_API_KEY") else 0
    bzzoiro = 150 if _present("BZZOIRO_API_KEY") else 0

    env: dict[str, Any] = {
        "CORE_COVERAGE_QUOTA_OVERRIDE_ENABLED": "true",
        "CORE_COVERAGE_QUOTA_OVERRIDE_VERSION": "v1-2026-05-13",
        "HARIZON_PRIMARY_PROVIDERS": "odds_api_io,bzzoiro,sstats",
        "HARIZON_ALLOWED_PROVIDER_SET": "odds_api_io,sstats,bzzoiro,football_data,thesportsdb,weatherapi,open_meteo,clubelo,allsportsapi,sportlogic",
        "ALL_SOURCES_FREE_MAXIMIZE": "true",
        "PROVIDER_REQUEST_BUDGET_MODE": "per_run_only",
        "PROVIDER_REQUEST_BUDGET_DISABLE_DAILY_MONTHLY": "true",
        "CONTEXT_ENRICHMENT_REQUIRES_OFFERS": "false",
        "DAY_INVENTORY_CONTEXT_BACKFILL_ENABLED": "true",
        "DAY_INVENTORY_CONTEXT_BACKFILL_LIMIT": "220",
        "DAY_INVENTORY_NEAR_WINDOW_PRIORITY": "true",
        "DAY_INVENTORY_NEAR_WINDOW_HOURS": "12",
        "MAX_MATCHES_FOR_ODDS_FETCH": "900",
        "ANALYSIS_MATCH_CAP_PER_RUN": "900",
        "DIAGNOSTICS_MATCH_LIMIT": "900",
        "CONTEXT_ENRICHMENT_MATCH_LIMIT": "320",
        "PREMIUM_CONTEXT_SHORTLIST_LIMIT": "160",
        "ODDS_API_IO_ENABLED": "true" if odds_total else "false",
        "ENABLE_ODDS_API_IO": "true" if odds_total else "false",
        "ODDS_API_IO_BOOKMAKERS": "Bet365,Unibet,Betfair Exchange,Sbobet",
        "ODDS_API_IO_BOOKMAKERS_ACCOUNT1": "Bet365,Unibet",
        "ODDS_API_IO_BOOKMAKERS_ACCOUNT2": "Betfair Exchange,Sbobet",
        "TARGET_BOOKMAKERS": "Bet365,Unibet,Betfair Exchange,Sbobet",
        "CONSENSUS_BOOKMAKERS": "Bet365,Unibet,Betfair Exchange,Sbobet",
        "ODDS_API_IO_ACCOUNT1_PER_RUN_MAX": str(odds1),
        "ODDS_API_IO_ACCOUNT2_PER_RUN_MAX": str(odds2),
        "ODDS_API_IO_ACCOUNT1_MAX_REQUESTS_PER_RUN": str(odds1),
        "ODDS_API_IO_ACCOUNT2_MAX_REQUESTS_PER_RUN": str(odds2),
        "ODDS_API_IO_MAX_EVENT_PAGES_PER_SPORT": "36",
        "ODDS_API_IO_PAGE_LIMIT": "100",
        "SECONDARY_ODDS_RESCUE_ENABLED": "true",
        "SECONDARY_ODDS_RESCUE_TRIGGER": "thin_primary_market_depth",
        "SECONDARY_ODDS_RESCUE_MIN_PRIMARY_OFFERS": "120",
        "SSTATS_ENABLED": "true" if sstats else "false",
        "ENABLE_SSTATS": "true" if sstats else "false",
        "ENABLE_SSTATS_CONTEXT": "true" if sstats else "false",
        "SSTATS_CONTEXT_ENABLED": "true" if sstats else "false",
        "SSTATS_CONTEXT_MATCH_LIMIT": "180",
        "SSTATS_RECENT_MATCHES": "10",
        "SSTATS_LOOKBACK_DAYS": "60",
        "SSTATS_DEEP_ENDPOINTS_ENABLED": "true",
        "SSTATS_DEEP_REQUESTS_MAX_PER_RUN": str(min(90, sstats)),
        "BZZOIRO_ENABLED": "true" if bzzoiro else "false",
        "ENABLE_BZZOIRO": "true" if bzzoiro else "false",
        "ENABLE_BZZOIRO_CONTEXT": "true" if bzzoiro else "false",
        "BZZOIRO_CONTEXT_ENABLED": "true" if bzzoiro else "false",
        "BZZOIRO_CONTEXT_MATCH_LIMIT": "180",
        "BZZOIRO_MAX_PAGES": "20",
        "BZZOIRO_PAGE_SIZE": "50",
        "BZZOIRO_V2_ENRICHMENT_ENABLED": "true",
        "BZZOIRO_V2_MATCH_LIMIT": "96",
        "BZZOIRO_V2_MAX_HTTP_REQUESTS_PER_RUN": str(bzzoiro),
        "BZZOIRO_V2_EVENTS_ENABLED": "true",
        "BZZOIRO_V2_STATS_ENABLED": "true",
        "BZZOIRO_V2_METADATA_ENABLED": "true",
        "BZZOIRO_V2_LINEUPS_ENABLED": "true",
        "BZZOIRO_V2_ODDS_ENABLED": "true",
        "BZZOIRO_CURRENT_ODDS_AS_SECONDARY_SOURCE": "true",
        "BZZOIRO_ODDS_REKEY_ENABLED": "true",
        "BZZOIRO_ODDS_REKEY_MIN_SCORE": "68",
        "API_COVERAGE_CONSENSUS_GUARD_ENABLED": "true",
        "API_COVERAGE_MIN_EXACT_ODDS_SOURCES": "2",
        "API_COVERAGE_MIN_EXACT_BOOKS": "2",
        "API_COVERAGE_MIN_CONTEXT_SOURCES": "2",
        "API_COVERAGE_CONTEXT_EXCLUDE_NEWS_WEATHER_FROM_CORE": "true",
        "API_COVERAGE_MAX_SOURCE_PRICE_DISPERSION_PCT": "18.0",
        "API_COVERAGE_MAX_SELECTED_PRICE_DRIFT_PCT": "8.0",
        "MIN_BOOKS_FOR_CONSENSUS": "2",
        "MIN_BOOKS_PUBLISH": "2",
        "MIN_SOURCES_PUBLISH": "2",
        "PUBLISH_MIN_ODDS_SOURCES": "2",
        "PUBLISH_MIN_CONTEXT_SOURCES": "2",
        "MIN_CONTEXT_SOURCES_PUBLISH": "2",
        "MARKET_DERIVED_MIN_BOOKS": "2",
        "MARKET_DERIVED_MIN_SOURCES": "2",
    }

    _set_many(env)
    env.update(_put_limit("ODDS_API_IO", odds_total))
    env.update(_put_limit("SSTATS", sstats))
    env.update(_put_limit("BZZOIRO", bzzoiro, "BZZOIRO_EVENTS_MAX_REQUESTS_PER_RUN", "BZZOIRO_PREDICTIONS_MAX_REQUESTS_PER_RUN"))

    report = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "installed",
        "odds_api_io_total": odds_total,
        "odds_api_io_account1": odds1,
        "odds_api_io_account2": odds2,
        "sstats": sstats,
        "bzzoiro": bzzoiro,
        "env_written_count": len(env),
        "notes": [
            "Runtime budgets are raised only for core coverage providers.",
            "Publication still requires two exact odds sources and two context sources.",
            "Context backfill no longer waits for offers, so SStats/Bzzoiro can cover near-window matches earlier.",
        ],
    }
    _write_report(report)
    return report
