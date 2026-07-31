from __future__ import annotations

"""Authoritative full-cohort API policy.

The old policy spent most secondary-provider quota only when odds-api.io was empty.
That can create candidates, but it can never produce 2 independent odds sources for
all 300 rows.  This policy makes every configured independent provider work on the
coverage deficit, persists its evidence and keeps publication guards unchanged.
"""

import json
import os
from datetime import UTC, datetime
from importlib import import_module
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
REPORT_PATH = ROOT / ".data" / "exports" / "latest-core-coverage-quota-runtime-override.json"
_INSTALLED = False


def _present(*names: str) -> bool:
    return any(str(os.getenv(name) or "").strip() for name in names)


def _write(payload: dict[str, Any]) -> None:
    try:
        REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        REPORT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except Exception:
        pass


def _apply(values: dict[str, Any]) -> None:
    for key, value in values.items():
        os.environ[str(key)] = str(value)
    github_env = str(os.getenv("GITHUB_ENV") or "").strip()
    if github_env:
        try:
            with open(github_env, "a", encoding="utf-8") as handle:
                for key in sorted(values):
                    handle.write(f"{key}={values[key]}\n")
        except Exception:
            pass


def _run_installer(module_name: str) -> dict[str, Any]:
    try:
        module = import_module(module_name)
        installer = getattr(module, "install", None)
        if not callable(installer):
            return {"status": "missing_install"}
        result = installer()
        return result if isinstance(result, dict) else {"status": "ok", "result": str(result)}
    except Exception as exc:
        return {"status": "error", "error": f"{type(exc).__name__}: {exc}"}


def install() -> dict[str, Any]:
    global _INSTALLED
    if _INSTALLED:
        return {"status": "already_installed"}
    _INSTALLED = True

    odds1 = 100 if _present("ODDS_API_IO_KEY") else 0
    odds2 = 100 if _present("ODDS_API_IO_KEY_2", "ODDS_API_IO_KEY2") else 0
    odds_total = odds1 + odds2
    sstats = 150 if _present("SSTATS_API_KEY") else 0
    bzzoiro = 200 if _present("BZZOIRO_API_KEY") else 0
    allsports = 96 if _present("ALLSPORTSAPI_API_KEY", "ALLSPORTSAPI_KEY") else 0
    sportlogic = 30 if _present("SPORTLOGIC_API_KEY", "SPORTLOGIC_KEY", "SPORTLOGIC_TOKEN") else 0
    football_data = 12 if _present("FOOTBALL_DATA_API_KEY", "FOOTBALL_DATA_KEY") else 0
    thesportsdb = 20 if _present("THESPORTSDB_API_KEY") else 8
    api_football = 40 if _present("API_FOOTBALL_KEY", "API_FOOTBALL_API_KEY") else 0

    env: dict[str, Any] = {
        "CORE_COVERAGE_QUOTA_OVERRIDE_ENABLED": "true",
        "CORE_COVERAGE_QUOTA_OVERRIDE_VERSION": "v4-verified-300-completion",
        "HARIZON_AUTONOMOUS_ACCUMULATION_MODE": "true",
        "HARIZON_PRIMARY_PROVIDERS": "odds_api_io,sstats_pari,bzzoiro,allsportsapi,sportlogic,sstats",
        "HARIZON_ALLOWED_PROVIDER_SET": "odds_api_io,sstats_pari,bzzoiro,allsportsapi,sportlogic,sstats,football_data,thesportsdb,espn,openligadb,openfootball,clubelo,api_football",
        "ALL_SOURCES_FREE_MAXIMIZE": "true",
        "PROVIDER_REQUEST_BUDGET_MODE": "hard_provider_caps",
        "PROVIDER_REQUEST_BUDGET_DISABLE_DAILY_MONTHLY": "false",
        "DAY_INVENTORY_TARGET_SIZE": "300",
        "DAY_INVENTORY_MAX_MATCHES": "300",
        "DAY_INVENTORY_FORCE_TOP_300": "true",
        "DAY_INVENTORY_FORCE_FULL_300": "true",
        "DAY_INVENTORY_FORCE_ALIAS_SHRINK": "true",
        "DAY_INVENTORY_PRESERVE_CACHED_EVIDENCE": "true",
        "DAY_INVENTORY_CONTEXT_BACKFILL_ENABLED": "true",
        "DAY_INVENTORY_CONTEXT_BACKFILL_LIMIT": "300",
        "CONTEXT_ENRICHMENT_REQUIRES_OFFERS": "false",
        "CONTEXT_ENRICHMENT_MATCH_LIMIT": "300",
        "PREMIUM_CONTEXT_SHORTLIST_LIMIT": "300",
        "MAX_MATCHES_FOR_ODDS_FETCH": "300",
        "ANALYSIS_MATCH_CAP_PER_RUN": "300",
        "DAILY_ANALYSIS_MATCH_LIMIT": "300",
        "DIAGNOSTICS_MATCH_LIMIT": "300",
        "RUNBOT_DISCOVERY_FIRST_FORCE_FULL_REFRESH": "true",
        "RUNBOT_DISCOVERY_FIRST_FULL_REFRESH_INTERVAL_MINUTES": "15",
        "RUNBOT_DISCOVERY_FIRST_MAX_SECONDS": "420",
        "RUNBOT_DISCOVERY_FIRST_FINAL_RESERVE_SECONDS": "25",
        "RUNBOT_INCREMENTAL_DEEP_ENRICHMENT_ENABLED": "true",
        "RUNBOT_INCREMENTAL_BZZOIRO_GAP_ENRICHMENT_ENABLED": "true",
        # odds-api.io: one provider, two accounts only increase request/book depth.
        "ODDS_API_IO_ENABLED": "true" if odds_total else "false",
        "ENABLE_ODDS_API_IO": "true" if odds_total else "false",
        "ODDS_API_IO_PER_RUN_MAX": str(odds_total),
        "ODDS_API_IO_MAX_HTTP_REQUESTS_PER_RUN": str(odds_total),
        "ODDS_API_IO_ACCOUNT1_PER_RUN_MAX": str(odds1),
        "ODDS_API_IO_ACCOUNT2_PER_RUN_MAX": str(odds2),
        "ODDS_API_IO_ACCOUNT1_MAX_REQUESTS_PER_RUN": str(odds1),
        "ODDS_API_IO_ACCOUNT2_MAX_REQUESTS_PER_RUN": str(odds2),
        "ODDS_API_IO_MAX_EVENT_PAGES_PER_SPORT": "20",
        "ODDS_API_IO_MAX_PAGES_PER_SPORT": "20",
        "ODDS_API_IO_PAGE_LIMIT": "100",
        "ODDS_API_IO_FETCH_FULL_DAY_INVENTORY": "true",
        "DAY_INVENTORY_ODDS_API_IO_TARGET_MATCHES": "300",
        "PRICE_BACKFILL_ODDS_API_IO_ENABLED": "true",
        "PRICE_BACKFILL_ODDS_API_IO_EVENT_LIMIT": "300",
        "PRICE_BACKFILL_ODDS_API_IO_BATCHES_PER_ACCOUNT": "10",
        # SStats context + independent Pari current line API.
        "SSTATS_ENABLED": "true" if sstats else "false",
        "ENABLE_SSTATS": "true" if sstats else "false",
        "ENABLE_SSTATS_CONTEXT": "true" if sstats else "false",
        "SSTATS_CONTEXT_ENABLED": "true" if sstats else "false",
        "SSTATS_MAX_HTTP_REQUESTS_PER_RUN": str(sstats),
        "SSTATS_REQUESTS_MAX_PER_RUN": str(sstats),
        "SSTATS_CONTEXT_MATCH_LIMIT": "300",
        "SSTATS_LOOKBACK_DAYS": "90",
        "SSTATS_REQUEST_CHUNK_DAYS": "10",
        "SSTATS_RECENT_MATCHES": "12",
        "SSTATS_FORM_MIN_SAMPLE_PER_TEAM": "2",
        "SSTATS_DEEP_ENRICHMENT_ENABLED": "true" if sstats else "false",
        "SSTATS_DEEP_ENRICHMENT_AFTER_CROSSWALK": "true" if sstats else "false",
        "SSTATS_DEEP_DETAIL_LIMIT_PER_RUN": "150",
        "SSTATS_DEEP_CONTEXT_MATCH_LIMIT": "300",
        "SSTATS_PARI_ODDS_ENABLED": "true" if sstats else "false",
        "SSTATS_PARI_DETAIL_MATCH_LIMIT": "300",
        "SSTATS_PARI_CONCURRENCY": "16",
        "SSTATS_PARI_TIMEOUT_SECONDS": "10",
        "SSTATS_CURRENT_ODDS_AS_LINE_SOURCE": "false",
        "SSTATS_ODDS_RESCUE_LIMIT_PER_RUN": "300",
        # Bzzoiro uses broad /odds/best first, then event details only for gaps.
        "BZZOIRO_ENABLED": "true" if bzzoiro else "false",
        "ENABLE_BZZOIRO": "true" if bzzoiro else "false",
        "ENABLE_BZZOIRO_CONTEXT": "true" if bzzoiro else "false",
        "BZZOIRO_CONTEXT_ENABLED": "true" if bzzoiro else "false",
        "BZZOIRO_PER_RUN_MAX": str(bzzoiro),
        "BZZOIRO_MAX_HTTP_REQUESTS_PER_RUN": str(bzzoiro),
        "BZZOIRO_REQUESTS_MAX_PER_RUN": str(bzzoiro),
        "BZZOIRO_REQUEST_BUDGET_GRANTED": str(bzzoiro),
        "BZZOIRO_CONTEXT_MATCH_LIMIT": "300",
        "BZZOIRO_ODDS_MATCH_LIMIT": "300",
        "BZZOIRO_V2_MATCH_LIMIT": "300",
        "BZZOIRO_V2_MAX_EVENTS": "1500",
        "BZZOIRO_V2_PAGE_SIZE": "200",
        "BZZOIRO_MAX_PAGES": "30",
        "BZZOIRO_PAGE_SIZE": "200",
        "BZZOIRO_V2_EVENTS_ENABLED": "true",
        "BZZOIRO_V2_STATS_ENABLED": "true",
        "BZZOIRO_V2_METADATA_ENABLED": "true",
        "BZZOIRO_V2_ODDS_ENABLED": "true",
        "BZZOIRO_V2_FETCH_EVENT_ODDS": "true",
        "BZZOIRO_V2_FETCH_ODDS_COMPARISON": "true",
        "BZZOIRO_CURRENT_ODDS_AS_SECONDARY_SOURCE": "true",
        "BZZOIRO_ODDS_COMPARISON_AS_SECONDARY_OFFERS": "true",
        "BZZOIRO_ODDS_BEST_MAX_PAGES_PER_MARKET": "6",
        "BZZOIRO_ODDS_BEST_PAGE_SIZE": "200",
        "BZZOIRO_BEST_ODDS_MARKETS": "1x2,over_under_15,over_under_25,over_under_35,btts",
        "CORE_ODDS_PATCH_MATCH_LIMIT": "300",
        # AllSportsAPI is an independent line source; never wait for primary-empty.
        "ENABLE_ALLSPORTSAPI": "true" if allsports else "false",
        "ALLSPORTSAPI_ENABLED": "true" if allsports else "false",
        "ALLSPORTSAPI_ONLY_IF_PRIMARY_ODDS_EMPTY": "false",
        "ALLSPORTSAPI_MATCH_LIMIT": "300",
        "ALLSPORTSAPI_PER_RUN_MAX": str(allsports),
        "ALLSPORTSAPI_MAX_HTTP_REQUESTS_PER_RUN": str(allsports),
        "ALLSPORTSAPI_REQUESTS_MAX_PER_RUN": str(allsports),
        "DAY_INVENTORY_ENABLE_ALLSPORTSAPI": "true" if allsports else "false",
        # OddsPapi provider is a no-op compatibility stub; keep its slot free for Pari.
        "ENABLE_ODDSPAPI": "false",
        "ODDSPAPI_ENABLED": "false",
        # SportLogic remains inside its documented 500/day and 10/min limits.
        "SPORTLOGIC_ENABLED": "true" if sportlogic else "false",
        "ENABLE_SPORTLOGIC": "true" if sportlogic else "false",
        "SPORTLOGIC_CONTROLLED_ODDS_ENABLED": "true" if sportlogic else "false",
        "SPORTLOGIC_PER_RUN_MAX": str(sportlogic),
        "SPORTLOGIC_MAX_HTTP_REQUESTS_PER_RUN": str(sportlogic),
        "SPORTLOGIC_REQUESTS_MAX_PER_RUN": str(sportlogic),
        "SPORTLOGIC_REQUEST_BUDGET_GRANTED": str(sportlogic),
        "SPORTLOGIC_MATCH_LIMIT": "300",
        "SPORTLOGIC_CONTEXT_MATCH_LIMIT": "100",
        "SPORTLOGIC_ODDS_MATCH_LIMIT": str(sportlogic),
        # Independent context APIs.
        "ENABLE_FOOTBALL_DATA_CONTEXT": "true" if football_data else "false",
        "FOOTBALL_DATA_ENABLED": "true" if football_data else "false",
        "FOOTBALL_DATA_CONTEXT_MATCH_LIMIT": "300",
        "FOOTBALL_DATA_REQUESTS_MAX_PER_RUN": str(football_data),
        "ENABLE_THESPORTSDB_CONTEXT": "true",
        "THESPORTSDB_CONTEXT_ENABLED": "true",
        "THESPORTSDB_CONTEXT_MATCH_LIMIT": "300",
        "THESPORTSDB_MAX_HTTP_REQUESTS_PER_RUN": str(thesportsdb),
        "ENABLE_ESPN_CONTEXT": "true",
        "ESPN_CONTEXT_MATCH_LIMIT": "300",
        "ENABLE_OPENLIGADB_CONTEXT": "true",
        "OPENLIGADB_CONTEXT_MATCH_LIMIT": "300",
        "ENABLE_OPENFOOTBALL_CONTEXT": "true",
        "OPENFOOTBALL_CONTEXT_MATCH_LIMIT": "300",
        "ENABLE_API_FOOTBALL": "true" if api_football else "false",
        "API_FOOTBALL_ENABLED": "true" if api_football else "false",
        "API_FOOTBALL_CONTEXT_MATCH_LIMIT": "300",
        "API_FOOTBALL_MAX_HTTP_REQUESTS_PER_RUN": str(api_football),
        # Exact publication contract: A=2/2/2, controlled B=1/2/1.
        "MIN_BOOKS_PUBLISH": "2",
        "PUBLISH_MIN_BOOKS": "2",
        "MIN_SOURCES_PUBLISH": "1",
        "PUBLISH_MIN_ODDS_SOURCES": "1",
        "PUBLISH_MIN_CONTEXT_SOURCES": "1",
        "MIN_CONTEXT_SOURCES_PUBLISH": "1",
        "PUBLISH_TIER_A_MIN_BOOKS": "2",
        "PUBLISH_TIER_A_MIN_ODDS_SOURCES": "2",
        "PUBLISH_TIER_A_MIN_CONTEXT_SOURCES": "2",
        "PUBLISH_TIER_B_MIN_BOOKS": "2",
        "PUBLISH_TIER_B_MIN_ODDS_SOURCES": "1",
        "PUBLISH_TIER_B_MIN_CONTEXT_SOURCES": "1",
        "API_COVERAGE_MIN_EXACT_BOOKS": "2",
        "CONTROLLED_FALLBACK_MIN_ODDS_SOURCES": "1",
        "CONTROLLED_FALLBACK_MIN_CONTEXT_SOURCES": "1",
        "CONTROLLED_FALLBACK_REQUIRE_2_ODDS_SOURCES_FOR_TELEGRAM": "false",
        "CONTROLLED_FALLBACK_REQUIRE_2_CONTEXT_SOURCES_FOR_TELEGRAM": "false",
        "ODDS_SOURCE_INDEPENDENCE_ENABLED": "true",
        "BOOKMAKER_QUORUM_ENABLED": "true",
    }
    _apply(env)

    patches = {
        "maximum_provider_policy": _run_installer("app.services.api_maximum_coverage_runtime_patch"),
        "daily_coverage_runtime": _run_installer("app.services.daily_coverage_runtime_patch"),
        "sstats_bzzoiro_odds_merge": _run_installer("app.services.sstats_bzzoiro_odds_merge_patch"),
        "api_full_data": _run_installer("app.services.api_full_data_runtime_patch"),
        "secondary_odds_rescue": _run_installer("app.services.secondary_odds_rescue_runtime_patch"),
        "strict_inventory_sync": _run_installer("app.services.strict_coverage_inventory_sync"),
    }
    report = {
        "created_at_utc": datetime.now(UTC).isoformat(),
        "status": "installed",
        "policy_version": env["CORE_COVERAGE_QUOTA_OVERRIDE_VERSION"],
        "available_request_caps": {"odds_api_io": odds_total, "sstats": sstats, "bzzoiro": bzzoiro, "allsportsapi": allsports, "sportlogic": sportlogic},
        "patches": patches,
        "strict_publication": {
            "A": {"min_exact_odds_sources": 2, "min_bookmakers": 2, "min_core_context_sources": 2},
            "B": {"min_exact_odds_sources": 1, "min_bookmakers": 2, "min_core_context_sources": 1},
        },
        "selection": "actual evidence first; discovery hints never counted as evidence",
        "publication_contract_relaxed": False,
    }
    _write(report)
    return report


__all__ = ["install"]
