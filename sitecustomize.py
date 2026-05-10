from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


def _set(name: str, value: str) -> None:
    os.environ[name] = str(value)


def _local_hour() -> int:
    tz_name = os.getenv("APP_TIMEZONE") or os.getenv("TZ") or "Europe/Moscow"
    try:
        tz = ZoneInfo(str(tz_name))
    except Exception:
        tz = timezone.utc
    return datetime.now(timezone.utc).astimezone(tz).hour


def _runtime_phase() -> str:
    explicit = str(os.getenv("HARIZON_RUN_PHASE") or os.getenv("RUN_PHASE") or "").strip().lower()
    if explicit:
        return explicit
    hour = _local_hour()
    if 0 <= hour <= 2:
        return "full_inventory"
    if 3 <= hour <= 10:
        return "morning_backfill"
    return "live_refresh"


def _apply_common_prediction_contract() -> None:
    common = {
        "HARIZON_PHASE_POLICY_ENABLED": "true",
        "DAY_INVENTORY_USE_FOR_RUN": "true",
        "DAY_INVENTORY_NEAR_WINDOW_PRIORITY": "true",
        "DAY_INVENTORY_NEAR_WINDOW_HOURS": "12",
        "CONTEXT_ENRICHMENT_REQUIRES_OFFERS": "true",
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
        "SECONDARY_ODDS_RESCUE_ENABLED": "true",
        "SECONDARY_ODDS_RESCUE_FORCE_RAPIDAPI_REFRESH": "true",
        "SECONDARY_ODDS_RESCUE_MIN_PRIMARY_OFFERS": "80",
        "BZZOIRO_ODDS_REKEY_ENABLED": "true",
        "BZZOIRO_ODDS_REKEY_MIN_SCORE": "70",
    }
    for key, value in common.items():
        _set(key, value)


def _phase_env(phase: str) -> dict[str, str]:
    base = {
        "MATCH_BOOTSTRAP_PROVIDER": "odds_api_io",
        "DAY_INVENTORY_BOOTSTRAP_PROVIDER": "odds_api_io",
        "FIXTURE_EXPANSION_ENABLED": "true",
        "RUN_DAYS_AHEAD": "1",
        "SECONDARY_ODDS_RESCUE_TRIGGER": "thin_primary_market_depth",
    }
    if phase == "full_inventory":
        base.update({"DAY_INVENTORY_FORCE_PROVIDER_MERGE": "true", "DAY_INVENTORY_COVERAGE_MAX_REBUILD": "true", "PUBLISH_WINDOW_HOURS": "24", "ODDS_API_IO_MAX_EVENT_PAGES_PER_SPORT": "36", "ODDS_API_IO_PAGE_LIMIT": "100", "MAX_MATCHES_FOR_ODDS_FETCH": "900", "ANALYSIS_MATCH_CAP_PER_RUN": "900", "DIAGNOSTICS_MATCH_LIMIT": "900", "CONTEXT_ENRICHMENT_MATCH_LIMIT": "120", "PREMIUM_CONTEXT_SHORTLIST_LIMIT": "48", "WEATHER_CONTEXT_MATCH_LIMIT": "12", "NEWSAPI_MATCH_LIMIT": "0", "GNEWS_MATCH_LIMIT": "0", "SECONDARY_ODDS_RESCUE_TRIGGER": "odds_api_io_empty_or_thin"})
    elif phase == "morning_backfill":
        base.update({"DAY_INVENTORY_FORCE_PROVIDER_MERGE": "false", "DAY_INVENTORY_COVERAGE_MAX_REBUILD": "false", "PUBLISH_WINDOW_HOURS": "12", "ODDS_API_IO_MAX_EVENT_PAGES_PER_SPORT": "24", "ODDS_API_IO_PAGE_LIMIT": "100", "MAX_MATCHES_FOR_ODDS_FETCH": "650", "ANALYSIS_MATCH_CAP_PER_RUN": "650", "DIAGNOSTICS_MATCH_LIMIT": "650", "CONTEXT_ENRICHMENT_MATCH_LIMIT": "260", "PREMIUM_CONTEXT_SHORTLIST_LIMIT": "96", "WEATHER_CONTEXT_MATCH_LIMIT": "24"})
    else:
        base.update({"DAY_INVENTORY_FORCE_PROVIDER_MERGE": "false", "DAY_INVENTORY_COVERAGE_MAX_REBUILD": "false", "PUBLISH_WINDOW_HOURS": "12", "ODDS_API_IO_MAX_EVENT_PAGES_PER_SPORT": "18", "ODDS_API_IO_PAGE_LIMIT": "100", "MAX_MATCHES_FOR_ODDS_FETCH": "520", "ANALYSIS_MATCH_CAP_PER_RUN": "520", "DIAGNOSTICS_MATCH_LIMIT": "520", "CONTEXT_ENRICHMENT_MATCH_LIMIT": "240", "PREMIUM_CONTEXT_SHORTLIST_LIMIT": "96", "WEATHER_CONTEXT_MATCH_LIMIT": "24"})
    return base


def _apply_phase_policy() -> None:
    phase = _runtime_phase()
    _set("HARIZON_RUN_PHASE_EFFECTIVE", phase)
    _apply_common_prediction_contract()
    env = _phase_env(phase)
    for key, value in env.items():
        _set(key, value)
    try:
        out = ROOT / ".data" / "exports" / "latest-run-phase-policy.env"
        out.parent.mkdir(parents=True, exist_ok=True)
        lines = [f"HARIZON_RUN_PHASE_EFFECTIVE={phase}"] + [f"{k}={v}" for k, v in sorted(env.items())]
        out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except Exception:
        pass


def _safe_install(label: str, installer) -> None:
    try:
        installer()
    except Exception as exc:
        try:
            print(f"root sitecustomize {label} skipped: {type(exc).__name__}: {exc}")
        except Exception:
            pass


def _install_runtime_guards() -> None:
    _safe_install("telegram safety", lambda: __import__("telegram_controlled_pick_safety").install())
    _safe_install("rapidapi schema", lambda: __import__("app.providers.rapidapi_odds_bridge_schema_patch", fromlist=["install"]).install())
    _safe_install("odds secondary", lambda: __import__("app.providers.odds_api_io_secondary_rescue_patch", fromlist=["install"]).install())
    # Important order: signal_stack creates Bzzoiro offer hints; rekey must wrap on top of it.
    _safe_install("signal stack", lambda: __import__("app.services.signal_stack_runtime_patch", fromlist=["install"]).install())
    _safe_install("bzzoiro odds rekey", lambda: __import__("app.services.bzzoiro_odds_rekey_runtime_patch", fromlist=["install"]).install())


try:
    _apply_phase_policy()
except Exception as exc:
    try:
        print(f"root sitecustomize phase policy skipped: {type(exc).__name__}: {exc}")
    except Exception:
        pass

_install_runtime_guards()
