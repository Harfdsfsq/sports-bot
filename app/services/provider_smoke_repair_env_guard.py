from __future__ import annotations

"""Authoritative env guard for provider-smoke repair workflows.

The workflow and quota scripts may write conservative defaults into GITHUB_ENV.
This module is imported by Python processes during provider-smoke, so it keeps the
chosen provider limits authoritative for the current process and for later steps.

It also freezes the provider-smoke target date for the whole workflow. Without
this, a run that starts before local midnight and finishes after local midnight
can build `.data/day_inventory/YYYY-MM-DD.json` for one date and then repair or
collect artifacts for the next date.

Requested caps:
* odds-api.io: 100 requests/hour;
* SStats: 150 requests/minute;
* Bzzoiro: 200 requests/minute;
* SportLogic: 500 requests/day.
"""

import atexit
import os
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

BROAD_REPAIR_ENV = {
    "APP_ENV": "provider-smoke-repair",
    "HARIZON_PROVIDER_PROBE_MODE": "true",
    "HARIZON_FAST_INVENTORY_LOCK": "false",
    "DAY_INVENTORY_FAST_MODE": "false",
    "DAY_INVENTORY_FORCE_PROVIDER_MERGE": "true",
    "DAY_INVENTORY_FORCE_FULL_300": "true",
    "DAY_INVENTORY_FORCE_FULL_ALLOW_SSTATS_OVER_HARD_CAP": "true",
    "DAY_INVENTORY_EXTRA_FIXTURES_ENABLED": "true",
    "DAY_INVENTORY_ENABLE_BZZOIRO": "true",
    "DAY_INVENTORY_ENABLE_ALLSPORTSAPI": "true",
    "DAY_INVENTORY_ENABLE_SPORTLOGIC": "true",
    "DAY_INVENTORY_ENABLE_SSTATS": "true",
    "DAY_INVENTORY_TOP_MATCHES_ENABLED": "true",
    "DAY_INVENTORY_CONTEXT_BACKFILL_ENABLED": "true",
    "CONTEXT_ENRICHMENT_REQUIRES_OFFERS": "false",
    "BZZOIRO_PROVIDER_SMOKE_ENABLED": "true",
    "BZZOIRO_ENABLED": "true",
    "ENABLE_BZZOIRO": "true",
    "ENABLE_BZZOIRO_CONTEXT": "true",
    "BZZOIRO_CONTEXT_ENABLED": "true",
    "SSTATS_ENABLED": "true",
    "ENABLE_SSTATS": "true",
    "ENABLE_SSTATS_CONTEXT": "true",
    "SSTATS_CONTEXT_ENABLED": "true",
    "SPORTLOGIC_ENABLED": "true",
    "ENABLE_SPORTLOGIC": "true",
    "ALLSPORTSAPI_ENABLED": "true",
    "ENABLE_ALLSPORTSAPI": "true",
    "PROVIDER_SMOKE_MATCHING_DIAGNOSTICS_ENABLED": "true",
    "PROVIDER_SMOKE_MATCHING_PROVIDERS": "sstats,bzzoiro,sportlogic",
    "API_FULL_SMOKE_ENABLED": "false",
    "API_FULL_SMOKE_BZZOIRO_ENABLED": "true",
    "API_FULL_SMOKE_FOOTBALL_DATA_ENABLED": "true",
    "API_FULL_SMOKE_ODDS_API_IO_ENABLED": "true",
    "API_FULL_SMOKE_SPORTLOGIC_ENABLED": "true",
    "API_FULL_SMOKE_SPORTLOGIC_DETAILS_ENABLED": "false",
    "PUBLICATION_ALLOWED_MARKET_FAMILIES": "totals,spreads",
    "HARIZON_ALLOWED_PUBLICATION_FAMILIES": "totals,spreads",
    "H2H_PUBLICATION_ENABLED": "false",
    "BTTS_PUBLICATION_ENABLED": "false",
    "DNB_PUBLICATION_ENABLED": "false",
    "DOUBLE_CHANCE_PUBLICATION_ENABLED": "false",
    "TEAM_TOTALS_PUBLICATION_ENABLED": "false",
    "TOTALS_PUBLICATION_ENABLED": "true",
    "SPREADS_PUBLICATION_ENABLED": "true",
}

RAISED_LIMIT_ENV = {
    "ODDS_API_IO_HOURLY_LIMIT": "100",
    "ODDS_API_IO_PER_RUN_MAX": "100",
    "ODDS_API_IO_ACCOUNT1_PER_RUN_MAX": "50",
    "ODDS_API_IO_ACCOUNT2_PER_RUN_MAX": "50",
    "ODDS_API_IO_MAX_HTTP_REQUESTS_PER_RUN": "100",
    "ODDS_API_IO_MAX_REQUESTS_PER_RUN": "100",
    "ODDS_API_IO_REQUEST_BUDGET_GRANTED": "100",
    "ODDS_API_IO_REQUESTS_MAX_PER_RUN": "100",
    "ODDS_API_IO_MAX_EVENT_PAGES_PER_SPORT": "10",
    "ODDS_API_IO_MAX_PAGES_PER_SPORT": "10",
    "ODDS_API_IO_PAGE_LIMIT": "100",
    "PRICE_BACKFILL_ODDS_API_IO_BATCHES_PER_ACCOUNT": "5",
    "PRICE_BACKFILL_ODDS_API_IO_MAX_EVENT_IDS_PER_REQUEST": "10",
    "PRICE_BACKFILL_ODDS_API_IO_EVENT_LIMIT": "100",
    "MAX_MATCHES_FOR_ODDS_FETCH": "300",
    "DAY_INVENTORY_ODDS_API_IO_TARGET_MATCHES": "220",

    "BZZOIRO_RATE_LIMIT_PER_MINUTE": "200",
    "BZZOIRO_PER_RUN_MAX": "200",
    "BZZOIRO_MAX_REQUESTS_PER_RUN": "200",
    "BZZOIRO_MAX_HTTP_REQUESTS_PER_RUN": "200",
    "BZZOIRO_REQUESTS_MAX_PER_RUN": "200",
    "BZZOIRO_REQUEST_BUDGET_GRANTED": "200",
    "BZZOIRO_EVENTS_MAX_REQUESTS_PER_RUN": "80",
    "BZZOIRO_PREDICTIONS_MAX_REQUESTS_PER_RUN": "40",
    "BZZOIRO_PREDICTIONS_MAX_PAGES": "10",
    "BZZOIRO_MAX_PAGES": "20",
    "BZZOIRO_V2_MAX_EVENTS": "600",
    "BZZOIRO_V2_PAGE_SIZE": "200",
    "BZZOIRO_CONTEXT_MATCH_LIMIT": "300",
    "BZZOIRO_PRICE_BACKFILL_ENABLED": "true",
    "BZZOIRO_PRICE_BACKFILL_TARGET_LIMIT": "80",
    "DAY_INVENTORY_BZZOIRO_MAX_REQUESTS": "200",
    "DAY_INVENTORY_BZZOIRO_MAX_PAGES": "20",

    "SSTATS_RATE_LIMIT_PER_MINUTE": "150",
    "SSTATS_PER_RUN_MAX": "150",
    "SSTATS_MAX_REQUESTS_PER_RUN": "150",
    "SSTATS_REQUESTS_MAX_PER_RUN": "150",
    "SSTATS_REQUEST_BUDGET_GRANTED": "150",
    "SSTATS_MAX_HTTP_REQUESTS_PER_RUN": "150",
    "SSTATS_CONTEXT_MATCH_LIMIT": "300",
    "SSTATS_DEEP_DETAIL_LIMIT_PER_RUN": "40",
    "SSTATS_DEEP_CONTEXT_MATCH_LIMIT": "80",
    "SSTATS_DEEP_ENRICHMENT_ENABLED": "true",
    "SSTATS_DEEP_ENRICHMENT_AFTER_CROSSWALK": "true",
    "SSTATS_DEEP_SMOKE_DETAIL_GAMES": "30",
    "SSTATS_GAME_DETAIL_ENABLED": "true",
    "SSTATS_LAST_GAMES_STATS_ENABLED": "true",
    "SSTATS_INJURIES_ENABLED": "false",
    "SSTATS_GLICKO_ENABLED": "true",
    "SSTATS_ODDS_RESCUE_ENABLED": "true",
    "DAY_INVENTORY_SSTATS_MAX_REQUESTS": "150",
    "DAY_INVENTORY_SSTATS_TOTAL_HARD_CAP": "300",
    "DAY_INVENTORY_FORCE_FULL_ALLOW_SSTATS_OVER_HARD_CAP": "true",

    "SPORTLOGIC_DAILY_LIMIT": "500",
    "SPORTLOGIC_ENABLED": "true",
    "ENABLE_SPORTLOGIC": "true",
    "SPORTLOGIC_CONTROLLED_ODDS_ENABLED": "true",
    "SPORTLOGIC_PER_RUN_MAX": "40",
    "SPORTLOGIC_MAX_HTTP_REQUESTS_PER_RUN": "40",
    "SPORTLOGIC_REQUESTS_MAX_PER_RUN": "40",
    "SPORTLOGIC_REQUEST_BUDGET_GRANTED": "40",
    "SPORTLOGIC_MATCH_LIMIT": "100",
    "SPORTLOGIC_CONTEXT_MATCH_LIMIT": "100",
    "SPORTLOGIC_ODDS_MATCH_LIMIT": "40",
    "SPORTLOGIC_ACTIVE_ODDS_SMOKE_PAGES": "2",
    "SPORTLOGIC_ACTIVE_ODDS_SMOKE_GAME_LIMIT": "40",
    "SPORTLOGIC_PER_PAGE": "50",
    "SPORTLOGIC_MATCH_MIN_SCORE": "48",
    "DAY_INVENTORY_ENABLE_SPORTLOGIC": "true",
    "DAY_INVENTORY_SPORTLOGIC_MATCH_LIMIT": "100",
    "DAY_INVENTORY_SPORTLOGIC_MAX_REQUESTS": "40",
}

MINIMAL_REPAIR_ENV = {
    **BROAD_REPAIR_ENV,
    "APP_ENV": "provider-smoke-minimal-repair",
    "PUBLISH_ALLOW_B_TIER": "true",
    "PUBLISH_COVERAGE_TIER_MODE": "hybrid",
    "CONTROLLED_FALLBACK_TELEGRAM_ALLOW_TIER_B": "true",
    "HARIZON_PROVIDER_PROBE_MODE": "true",
    "DAY_INVENTORY_TARGET_SIZE": "300",
    "DAY_INVENTORY_MAX_MATCHES": "300",
    "DAY_INVENTORY_FORCE_TOP_300": "true",
    "DAY_INVENTORY_FORCE_FULL_300": "true",
    "DAY_INVENTORY_FORCE_PROVIDER_MERGE": "true",
    "DAY_INVENTORY_ENABLE_BZZOIRO": "true",
    "DAY_INVENTORY_ENABLE_ALLSPORTSAPI": "false",
    "DAY_INVENTORY_ENABLE_SSTATS": "true",
    "DAY_INVENTORY_TOP_MATCHES_ENABLED": "true",
    "PROVIDER_SMOKE_FAST_PROVIDERS": "odds_api_io,bzzoiro,sstats,sportlogic",
    "PROVIDER_SMOKE_MATCHING_PROVIDERS": "sstats,bzzoiro,sportlogic",
    "PROVIDER_SMOKE_MATCHING_ODDS_LIMIT": "160",
    "PROVIDER_SMOKE_MATCHING_ODDS_PAGES": "4",
    "PROVIDER_SMOKE_REPEATS": "1",
    "API_FULL_SMOKE_ENABLED": "false",
    "API_FULL_SMOKE_SPORTLOGIC_ENABLED": "true",
    "API_FULL_SMOKE_SPORTLOGIC_DETAILS_ENABLED": "false",
    "API_FULL_SMOKE_ODDS_EXTRA_MAX_REQUESTS": "10",
    "API_FULL_SMOKE_ODDS_EVENT_LIMIT": "20",
    "PUBLISH_MIN_ODDS_SOURCES": "1",
    "PUBLISH_MIN_CONTEXT_SOURCES": "2",
    "MIN_CONTEXT_SOURCES_PUBLISH": "2",
    "CONTROLLED_FALLBACK_MIN_ODDS_SOURCES": "1",
    **RAISED_LIMIT_ENV,
}

PRESERVE_IF_SET = {
    "DAY_INVENTORY_TARGET_DATE",
    "PROVIDER_SMOKE_TARGET_DATE",
    "DAY_INVENTORY_CACHE_DATE",
    "PROVIDER_SMOKE_COVERAGE_TARGET",
    "PROVIDER_SMOKE_FAST_MAX_SECONDS",
    "PROVIDER_SMOKE_FAST_TIMEOUT",
    "PROVIDER_SMOKE_FAST_CONCURRENCY",
    "PROVIDER_API_MIN_PROBE_TIMEOUT",
    "PROVIDER_SMOKE_SHOW_OK_SAMPLES",
}


def _is_truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on", "force"}


def _is_minimal_mode() -> bool:
    app_env = str(os.getenv("APP_ENV") or "").strip().lower()
    return app_env == "provider-smoke-minimal-repair" or _is_truthy(os.getenv("PROVIDER_SMOKE_MINIMAL_REPAIR"))


def _is_provider_smoke_repair() -> bool:
    app_env = str(os.getenv("APP_ENV") or "").strip().lower()
    return app_env in {"provider-smoke-repair", "provider-smoke-minimal-repair"} or _is_truthy(os.getenv("PROVIDER_SMOKE_REPAIR_ENV_GUARD_ENABLED"))


def _base_env() -> dict[str, str]:
    return dict(MINIMAL_REPAIR_ENV if _is_minimal_mode() else {**BROAD_REPAIR_ENV, **RAISED_LIMIT_ENV})


def _app_tz() -> ZoneInfo:
    try:
        return ZoneInfo(os.getenv("APP_TIMEZONE") or os.getenv("TZ") or "Europe/Moscow")
    except Exception:
        return ZoneInfo("Europe/Moscow")


def _frozen_target_date() -> str:
    for key in ("DAY_INVENTORY_TARGET_DATE", "PROVIDER_SMOKE_TARGET_DATE", "DAY_INVENTORY_CACHE_DATE"):
        value = str(os.getenv(key) or "").strip()
        if value:
            return value
    return datetime.now(timezone.utc).astimezone(_app_tz()).date().isoformat()


def _effective_env() -> dict[str, str]:
    values: dict[str, str] = {}
    base = _base_env()
    for key, default in base.items():
        current = os.getenv(key)
        if key in PRESERVE_IF_SET and current not in (None, ""):
            values[key] = str(current)
        else:
            values[key] = str(default)
    target_date = _frozen_target_date()
    values["DAY_INVENTORY_TARGET_DATE"] = target_date
    values["PROVIDER_SMOKE_TARGET_DATE"] = target_date
    values["DAY_INVENTORY_CACHE_DATE"] = str(os.getenv("DAY_INVENTORY_CACHE_DATE") or target_date)
    values["PROVIDER_SMOKE_TARGET_DATE_FROZEN"] = "true"
    return values


def _write_github_env() -> None:
    path = os.getenv("GITHUB_ENV")
    if not path:
        return
    values = _effective_env()
    try:
        with Path(path).open("a", encoding="utf-8") as fh:
            for key in sorted(values):
                fh.write(f"{key}={values[key]}\n")
    except Exception:
        pass


def install() -> None:
    if not _is_provider_smoke_repair():
        return
    for key, value in _effective_env().items():
        os.environ[str(key)] = str(value)
    _write_github_env()
    atexit.register(_write_github_env)


install()
