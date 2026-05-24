from __future__ import annotations

"""Final provider tier runtime guard.

Business rule:
- The three primary providers are odds-api.io, Bzzoiro and SStats.
- They are allowed to spend up to ~100 requests per run each because they carry
  fixture discovery, odds and core context.
- Every other provider is supplemental and should run only after shortlisting or
  for explicit missing roles: weather, ClubElo, news, mapping and odds rescue.

This module is intentionally installed near the end of the explicit runtime
startup chain. It rewrites both os.environ and GITHUB_ENV so older quota scripts
cannot silently restore conservative defaults such as BZZOIRO=2 or SSTATS=12. It
also exports the effective tier contract so provider-smoke/run-bot artifacts show
the real final contract, not the earlier conservative contract written by older
scripts.
"""

import atexit
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

UTC = timezone.utc
OUT_DIR = Path(".data/exports")
PRIMARY_CONTRACT_OUT = OUT_DIR / "latest-primary-provider-tier-contract.json"
PER_RUN_CONTRACT_OUT = OUT_DIR / "latest-per-run-api-quota-contract.json"

PRIMARY_PROVIDERS = "odds_api_io,bzzoiro,sstats"
SUPPLEMENTAL_PROVIDERS = (
    "thesportsdb,football_data,allsportsapi,clubelo,open_meteo,weatherapi,"
    "openweathermap,newsapi,currents,guardian,newsdata,gnews,wikidata,highlightly,"
    "football_data_co_uk,sportlogic,futrixmetrics,meteostat"
)

PRIMARY_ENV = {
    "HARIZON_PROVIDER_TIER_STRATEGY_VERSION": "primary-three-v1-100-per-run",
    "HARIZON_PRIMARY_PROVIDERS": PRIMARY_PROVIDERS,
    "HARIZON_SUPPLEMENTAL_PROVIDERS": SUPPLEMENTAL_PROVIDERS,
    "HARIZON_PRIMARY_PROVIDER_MAX_REQUESTS_PER_RUN": "100",
    "HARIZON_PRIMARY_ODDS_PROVIDER_MAX_REQUESTS_PER_RUN": "200",
    "HARIZON_PROVIDER_PIPELINE_ORDER": "primary_inventory_lines_context,primary_model_shortlist,supplemental_top_pick_backfill,publication_guard",
    "HARIZON_SUPPLEMENTAL_API_MODE": "top_pick_backfill_only",
    "SUPPLEMENTAL_PROVIDERS_REQUIRE_SHORTLIST": "true",
    "SUPPLEMENTAL_PROVIDERS_REQUIRE_MISSING_ROLE": "true",
    "SUPPLEMENTAL_BACKFILL_AFTER_PRIMARY_SHORTLIST": "true",
    "SUPPLEMENTAL_BACKFILL_TOP_MATCH_LIMIT": "20",
    "SUPPLEMENTAL_BACKFILL_TOP_PICK_LIMIT": "8",
    "SUPPLEMENTAL_BACKFILL_NEAR_WINDOW_HOURS": "12",
    "SUPPLEMENTAL_BACKFILL_ALLOWED_ROLES": "weather,news,clubelo,rating,mapping,venue,injuries,lineups,odds_rescue",
    "CONTEXT_ENRICHMENT_REQUIRES_OFFERS": "false",
    "DAY_INVENTORY_CONTEXT_BACKFILL_ENABLED": "true",
    "DAY_INVENTORY_CONTEXT_BACKFILL_LIMIT": "300",
    "DAY_INVENTORY_TOP_MATCHES_ENABLED": "true",
    "DAY_INVENTORY_NEAR_WINDOW_PRIORITY": "true",
    "DAY_INVENTORY_FORCE_PROVIDER_MERGE": "true",
    "DAY_INVENTORY_FORCE_FULL_300": "true",
    "ENABLE_ODDS_API_IO": "true",
    "ODDS_API_IO_ENABLED": "true",
    "ODDS_API_IO_MAX_REQUESTS_PER_RUN": "200",
    "ODDS_API_IO_MAX_HTTP_REQUESTS_PER_RUN": "200",
    "ODDS_API_IO_PER_RUN_MAX": "200",
    "ODDS_API_IO_REQUEST_BUDGET_GRANTED": "200",
    "ODDS_API_IO_ACCOUNT1_PER_RUN_MAX": "100",
    "ODDS_API_IO_ACCOUNT1_MAX_REQUESTS_PER_RUN": "100",
    "ODDS_API_IO_ACCOUNT2_PER_RUN_MAX": "100",
    "ODDS_API_IO_ACCOUNT2_MAX_REQUESTS_PER_RUN": "100",
    "ODDS_API_IO_PAGE_LIMIT": "100",
    "ODDS_API_IO_MAX_EVENT_PAGES_PER_SPORT": "36",
    "ODDS_API_IO_BOOKMAKERS": "Bet365,Unibet,Betfair Exchange,Sbobet",
    "ODDS_API_IO_BOOKMAKERS_ACCOUNT1": "Bet365,Unibet",
    "ODDS_API_IO_BOOKMAKERS_ACCOUNT2": "Betfair Exchange,Sbobet",
    "TARGET_BOOKMAKERS": "Bet365,Unibet,Betfair Exchange,Sbobet",
    "CONSENSUS_BOOKMAKERS": "Bet365,Unibet,Betfair Exchange,Sbobet",
    "ENABLE_BZZOIRO": "true",
    "BZZOIRO_ENABLED": "true",
    "ENABLE_BZZOIRO_CONTEXT": "true",
    "BZZOIRO_CONTEXT_ENABLED": "true",
    "BZZOIRO_MAX_HTTP_REQUESTS_PER_RUN": "100",
    "BZZOIRO_MAX_REQUESTS_PER_RUN": "100",
    "BZZOIRO_PER_RUN_MAX": "100",
    "BZZOIRO_REQUEST_BUDGET_GRANTED": "100",
    "BZZOIRO_EVENTS_MAX_REQUESTS_PER_RUN": "50",
    "BZZOIRO_PREDICTIONS_MAX_REQUESTS_PER_RUN": "50",
    "BZZOIRO_CONTEXT_MATCH_LIMIT": "300",
    "BZZOIRO_MAX_PAGES": "20",
    "BZZOIRO_PAGE_SIZE": "50",
    "BZZOIRO_REQUEST_RETRIES": "1",
    "BZZOIRO_RETRY_BACKOFF_SECONDS": "1",
    "ENABLE_SSTATS": "true",
    "SSTATS_ENABLED": "true",
    "ENABLE_SSTATS_CONTEXT": "true",
    "SSTATS_CONTEXT_ENABLED": "true",
    "SSTATS_REQUESTS_MAX_PER_RUN": "100",
    "SSTATS_MAX_HTTP_REQUESTS_PER_RUN": "100",
    "SSTATS_MAX_REQUESTS_PER_RUN": "100",
    "SSTATS_PER_RUN_MAX": "100",
    "SSTATS_REQUEST_BUDGET_GRANTED": "100",
    "SSTATS_CONTEXT_MATCH_LIMIT": "300",
    "SSTATS_LOOKBACK_DAYS": "45",
    "SSTATS_RECENT_MATCHES": "10",
    "SSTATS_DEEP_ENRICHMENT_ENABLED": "true",
    "SSTATS_DEEP_ENRICHMENT_AFTER_CROSSWALK": "true",
    "SSTATS_DEEP_CONTEXT_MATCH_LIMIT": "300",
    "SSTATS_DEEP_DETAIL_LIMIT_PER_RUN": "100",
    "SSTATS_GLICKO_ENABLED": "true",
    "SSTATS_LAST_GAMES_STATS_ENABLED": "true",
    "SSTATS_GAME_DETAIL_ENABLED": "true",
    "SSTATS_INJURIES_ENABLED": "true",
    "SSTATS_ODDS_RESCUE_ENABLED": "true",
    "SSTATS_ODDS_RESCUE_ONLY_IF_ODDS_SOURCES_LT": "2",
}

SUPPLEMENTAL_ENV = {
    "THESPORTSDB_CONTEXT_MATCH_LIMIT": "30",
    "THESPORTSDB_MAX_REQUESTS_PER_RUN": "10",
    "FOOTBALL_DATA_CONTEXT_MATCH_LIMIT": "30",
    "FOOTBALL_DATA_MAX_REQUESTS_PER_RUN": "6",
    "FOOTBALL_DATA_CACHE_TTL_MINUTES": "720",
    "ALLSPORTSAPI_MATCH_LIMIT": "20",
    "ALLSPORTSAPI_MAX_REQUESTS_PER_RUN": "4",
    "ALLSPORTSAPI_ONLY_IF_MAPPING_OR_ODDS_MISSING": "true",
    "CLUBELO_ENABLED": "true",
    "ENABLE_CLUBELO": "true",
    "CLUBELO_MAX_REQUESTS_PER_RUN": "3",
    "CLUBELO_SHORTLIST_ONLY": "true",
    "OPEN_METEO_ENABLED": "true",
    "ENABLE_OPEN_METEO": "true",
    "OPEN_METEO_MAX_REQUESTS_PER_RUN": "30",
    "OPEN_METEO_SHORTLIST_ONLY": "true",
    "WEATHER_CONTEXT_ENABLED": "true",
    "WEATHER_CONTEXT_MATCH_LIMIT": "12",
    "WEATHERAPI_MAX_REQUESTS_PER_RUN": "8",
    "OPENWEATHERMAP_MAX_REQUESTS_PER_RUN": "4",
    "NEWS_INJURY_SHORTLIST_ENABLED": "true",
    "NEWS_INJURY_SHORTLIST_LIMIT": "8",
    "NEWS_CONTEXT_SHORTLIST_ONLY": "true",
    "NEWSAPI_MAX_REQUESTS_PER_RUN": "4",
    "CURRENTS_MAX_REQUESTS_PER_RUN": "4",
    "GUARDIAN_MAX_REQUESTS_PER_RUN": "4",
    "NEWSDATA_MAX_REQUESTS_PER_RUN": "4",
    "GNEWS_MAX_REQUESTS_PER_RUN": "2",
    "WIKIDATA_MAX_REQUESTS_PER_DAY": "8",
    "WIKIDATA_SPARQL_MAX_REQUESTS_PER_DAY": "2",
    "HIGHLIGHTLY_MAX_REQUESTS_PER_RUN": "4",
    "FUTRIXMETRICS_MAX_REQUESTS_PER_RUN": "4",
    "METEOSTAT_RAPIDAPI_MAX_REQUESTS_PER_RUN": "1",
    "SPORTLOGIC_MAX_REQUESTS_PER_RUN": "0",
    "SPORTLOGIC_ENABLED": "false",
    "ENABLE_SPORTLOGIC": "false",
    "SPORTLOGIC_CONTROLLED_ODDS_ENABLED": "false",
}

PRESERVE_IF_SET = {
    "API_FULL_SMOKE_ENABLED",
    "API_FULL_SMOKE_SPORTLOGIC_ENABLED",
    "API_FULL_SMOKE_SPORTLOGIC_DETAILS_ENABLED",
    "PROVIDER_SMOKE_MATCHING_PROVIDERS",
    "SPORTLOGIC_ACTIVE_ODDS_SMOKE_PAGES",
    "SPORTLOGIC_ACTIVE_ODDS_SMOKE_GAME_LIMIT",
}


def _present(*names: str) -> bool:
    return any(str(os.getenv(name) or "").strip() for name in names)


def _effective_env() -> dict[str, str]:
    env: dict[str, str] = {}
    env.update(PRIMARY_ENV)
    env.update(SUPPLEMENTAL_ENV)
    if not _present("ODDS_API_IO_KEY_2", "ODDS_API_IO_KEY2"):
        env["ODDS_API_IO_ACCOUNT2_PER_RUN_MAX"] = "0"
        env["ODDS_API_IO_ACCOUNT2_MAX_REQUESTS_PER_RUN"] = "0"
    for key in list(env):
        if key in PRESERVE_IF_SET and os.getenv(key) not in (None, ""):
            env[key] = str(os.getenv(key))
    return env


def _write_github_env(values: dict[str, str]) -> None:
    path = os.getenv("GITHUB_ENV")
    if not path:
        return
    try:
        with Path(path).open("a", encoding="utf-8") as fh:
            for key in sorted(values):
                fh.write(f"{key}={values[key]}\n")
    except Exception:
        pass


def _contract(values: dict[str, str]) -> dict[str, Any]:
    return {
        "created_at_utc": datetime.now(UTC).isoformat(),
        "contract_version": values.get("HARIZON_PROVIDER_TIER_STRATEGY_VERSION"),
        "source": "app.services.primary_provider_tier_runtime_guard final env layer",
        "primary_providers": PRIMARY_PROVIDERS.split(","),
        "supplemental_providers": SUPPLEMENTAL_PROVIDERS.split(","),
        "pipeline_order": values.get("HARIZON_PROVIDER_PIPELINE_ORDER"),
        "supplemental_mode": values.get("HARIZON_SUPPLEMENTAL_API_MODE"),
        "per_run_grants": {
            "odds_api_io": int(values.get("ODDS_API_IO_MAX_REQUESTS_PER_RUN") or 0),
            "odds_api_io_account1": int(values.get("ODDS_API_IO_ACCOUNT1_PER_RUN_MAX") or 0),
            "odds_api_io_account2": int(values.get("ODDS_API_IO_ACCOUNT2_PER_RUN_MAX") or 0),
            "bzzoiro": int(values.get("BZZOIRO_MAX_HTTP_REQUESTS_PER_RUN") or 0),
            "sstats": int(values.get("SSTATS_MAX_HTTP_REQUESTS_PER_RUN") or 0),
            "thesportsdb": int(values.get("THESPORTSDB_MAX_REQUESTS_PER_RUN") or 0),
            "football_data": int(values.get("FOOTBALL_DATA_MAX_REQUESTS_PER_RUN") or 0),
            "allsportsapi": int(values.get("ALLSPORTSAPI_MAX_REQUESTS_PER_RUN") or 0),
            "clubelo": int(values.get("CLUBELO_MAX_REQUESTS_PER_RUN") or 0),
            "open_meteo": int(values.get("OPEN_METEO_MAX_REQUESTS_PER_RUN") or 0),
            "weatherapi": int(values.get("WEATHERAPI_MAX_REQUESTS_PER_RUN") or 0),
            "newsapi": int(values.get("NEWSAPI_MAX_REQUESTS_PER_RUN") or 0),
            "currents": int(values.get("CURRENTS_MAX_REQUESTS_PER_RUN") or 0),
            "guardian": int(values.get("GUARDIAN_MAX_REQUESTS_PER_RUN") or 0),
            "newsdata": int(values.get("NEWSDATA_MAX_REQUESTS_PER_RUN") or 0),
            "wikidata_day": int(values.get("WIKIDATA_MAX_REQUESTS_PER_DAY") or 0),
            "highlightly": int(values.get("HIGHLIGHTLY_MAX_REQUESTS_PER_RUN") or 0),
            "sportlogic": int(values.get("SPORTLOGIC_MAX_REQUESTS_PER_RUN") or 0),
        },
        "policy": {
            "primary_api_budget": "up to 100 requests/run each for Bzzoiro and SStats; odds-api.io up to 100/account/run",
            "supplemental_api_budget": "shortlist/backfill only after primary providers produce candidates or missing-role queue",
            "publication_guard": "unchanged: publication still requires 2 odds sources and allowed market families",
        },
        "env_written_count": len(values),
    }


def _write_contract_snapshot(values: dict[str, str]) -> None:
    try:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        payload = _contract(values)
        text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        PRIMARY_CONTRACT_OUT.write_text(text, encoding="utf-8")
        # Overwrite the older per-run contract artifact with the effective final
        # contract so logs match runtime behavior after all guards have run.
        PER_RUN_CONTRACT_OUT.write_text(text, encoding="utf-8")
    except Exception:
        pass


def install() -> bool:
    values = _effective_env()
    for key, value in values.items():
        os.environ[str(key)] = str(value)
    _write_github_env(values)
    _write_contract_snapshot(values)
    atexit.register(lambda: _write_github_env(_effective_env()))
    atexit.register(lambda: _write_contract_snapshot(_effective_env()))
    return True
