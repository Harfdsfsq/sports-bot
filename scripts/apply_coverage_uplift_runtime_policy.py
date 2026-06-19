from __future__ import annotations

"""Apply a targeted coverage-uplift runtime policy for HARIZON.

The script only changes data-collection budgets and coverage/enrichment limits.
It deliberately keeps publication gates strict: 2h publish window, daily B-tier cap,
line movement recheck, price-integrity and quality/value guards remain intact.
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
from typing import Any

ROOT = Path(".").resolve()
EXPORT_PATH = ROOT / ".data" / "exports" / "latest-coverage-uplift-runtime-policy.json"
GITHUB_ENV = os.getenv("GITHUB_ENV")


def _truthy(value: object, default: bool = False) -> bool:
    raw = str(value if value is not None else "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on", "force"}


def _present(*names: str) -> bool:
    return any(str(os.getenv(name) or "").strip() for name in names)


def _local_phase() -> str:
    explicit = str(os.getenv("HARIZON_RUN_PHASE") or os.getenv("RUN_PHASE") or "").strip().lower()
    if explicit:
        return explicit
    tz_name = os.getenv("APP_TIMEZONE") or os.getenv("TZ") or "Europe/Moscow"
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = timezone.utc
    hour = datetime.now(timezone.utc).astimezone(tz).hour
    if 0 <= hour <= 2:
        return "full_inventory"
    if 3 <= hour <= 10:
        return "morning_backfill"
    return "live_refresh"


def _put(env: dict[str, str], key: str, value: Any) -> None:
    env[str(key)] = str(value)


def _put_limit(env: dict[str, str], prefix: str, value: int, *extra_aliases: str) -> None:
    upper = prefix.upper()
    aliases = {
        f"{upper}_PER_RUN_MAX",
        f"{upper}_MAX_HTTP_REQUESTS_PER_RUN",
        f"{upper}_MAX_REQUESTS_PER_RUN",
        f"{upper}_REQUESTS_MAX_PER_RUN",
        f"{upper}_REQUEST_BUDGET_GRANTED",
        *extra_aliases,
    }
    for alias in aliases:
        env[alias] = str(max(0, int(value)))


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
    enabled = _truthy(os.getenv("HARIZON_COVERAGE_UPLIFT_ENABLED"), True)
    phase = _local_phase()
    if not enabled:
        payload = {
            "status": "disabled_by_env",
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "phase": phase,
        }
        EXPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        EXPORT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    # Conservative but wider than the live_refresh contract that was only giving
    # ~107/300 context coverage and ~60 fresh odds matches. Odds-api.io is still
    # kept under a safe per-run cap; SStats/Bzzoiro are lifted mainly by match
    # limits/pages so the runner can spend already-granted budget on coverage.
    if phase == "full_inventory":
        odds_total, odds_account = 100, 50
        sstats, bzzoiro = 150, 200
        context_limit, premium_limit = 300, 220
        bzz_pages, bzz_page_size = 24, 30
        sstats_deep, sstats_deep_context = 90, 200
        price_event_limit, price_batches = 180, 8
    elif phase == "morning_backfill":
        odds_total, odds_account = 100, 50
        sstats, bzzoiro = 140, 180
        context_limit, premium_limit = 300, 200
        bzz_pages, bzz_page_size = 22, 30
        sstats_deep, sstats_deep_context = 80, 180
        price_event_limit, price_batches = 160, 7
    else:
        odds_total, odds_account = 100, 50
        sstats, bzzoiro = 120, 140
        context_limit, premium_limit = 280, 180
        bzz_pages, bzz_page_size = 18, 25
        sstats_deep, sstats_deep_context = 70, 160
        price_event_limit, price_batches = 140, 6

    has_odds_1 = _present("ODDS_API_IO_KEY")
    has_odds_2 = _present("ODDS_API_IO_KEY_2", "ODDS_API_IO_KEY2")
    has_sstats = _present("SSTATS_API_KEY")
    has_bzz = _present("BZZOIRO_API_KEY")

    env: dict[str, str] = {
        "HARIZON_COVERAGE_UPLIFT_VERSION": "v18-lines-context-uplift",
        "HARIZON_COVERAGE_UPLIFT_PHASE": phase,
        "HARIZON_COVERAGE_UPLIFT_ENABLED": "true",
        # Keep v17 publishing discipline. Some older budget contracts write wider
        # publication windows, so restate the effective publication guard here.
        "PUBLISH_WINDOW_HOURS": "2",
        "CONTROLLED_FALLBACK_PUBLISH_WINDOW_HOURS": "2",
        "CONTROLLED_FALLBACK_DAILY_MAX_PUBLISHED": os.getenv("CONTROLLED_FALLBACK_DAILY_MAX_PUBLISHED") or "3",
        "CONTROLLED_FALLBACK_DAILY_MAX_B_TIER": os.getenv("CONTROLLED_FALLBACK_DAILY_MAX_B_TIER") or "3",
        "CONTROLLED_FALLBACK_DAILY_LIMIT_TIMEZONE": os.getenv("CONTROLLED_FALLBACK_DAILY_LIMIT_TIMEZONE") or "Europe/Moscow",
        "CONTROLLED_FALLBACK_DAILY_LIMIT_USE_PUBLISHED_AT": "true",
        # Inventory/coverage expansion.
        "DAY_INVENTORY_TARGET_SIZE": "300",
        "DAY_INVENTORY_MAX_MATCHES": "300",
        "DAY_INVENTORY_FORCE_FULL_300": "true",
        "DAY_INVENTORY_FORCE_TOP_300": "true",
        "DAY_INVENTORY_FORCE_PROVIDER_MERGE": "true",
        "DAY_INVENTORY_COVERAGE_MAX_REBUILD": "true",
        "DAY_INVENTORY_USE_FOR_RUN": "true",
        "DAY_INVENTORY_CONTEXT_BACKFILL_ENABLED": "true",
        "DAY_INVENTORY_CONTEXT_BACKFILL_LIMIT": "300",
        "DAY_INVENTORY_NEAR_WINDOW_PRIORITY": "true",
        "DAY_INVENTORY_NEAR_WINDOW_HOURS": "24",
        "CONTEXT_ENRICHMENT_REQUIRES_OFFERS": "false",
        "CONTEXT_ENRICHMENT_MATCH_LIMIT": str(context_limit),
        "PREMIUM_CONTEXT_SHORTLIST_LIMIT": str(premium_limit),
        "ANALYSIS_MATCH_CAP_PER_RUN": "900",
        "DIAGNOSTICS_MATCH_LIMIT": "900",
        "MAX_MATCHES_FOR_ODDS_FETCH": "300",
        "RUN_DAYS_AHEAD": "1",
        # Odds-api line backfill: increase targets without breaching the user
        # 100/hour expectation from the provider documentation.
        "DAY_INVENTORY_ODDS_API_IO_TARGET_MATCHES": "300",
        "DAY_INVENTORY_MULTI_SOURCE_MAX_MATCHES": "300",
        "ODDS_API_IO_MAX_EVENT_PAGES_PER_SPORT": "24",
        "ODDS_API_IO_MAX_PAGES_PER_SPORT": "24",
        "ODDS_API_IO_PAGE_LIMIT": "100",
        "ODDS_API_IO_ACCOUNT1_PER_RUN_MAX": str(odds_account if has_odds_1 else 0),
        "ODDS_API_IO_ACCOUNT2_PER_RUN_MAX": str(odds_account if has_odds_2 else 0),
        "ODDS_API_IO_ACCOUNT1_MAX_REQUESTS_PER_RUN": str(odds_account if has_odds_1 else 0),
        "ODDS_API_IO_ACCOUNT2_MAX_REQUESTS_PER_RUN": str(odds_account if has_odds_2 else 0),
        "PRICE_BACKFILL_ODDS_API_IO_BATCHES_PER_ACCOUNT": str(price_batches),
        "PRICE_BACKFILL_ODDS_API_IO_MAX_EVENT_IDS_PER_REQUEST": "12",
        "PRICE_BACKFILL_ODDS_API_IO_EVENT_LIMIT": str(price_event_limit),
        "ODDS_API_IO_RELAXED_INVENTORY_MATCHING_ENABLED": "true",
        "ODDS_API_IO_RELAXED_EXACT_TOLERANCE_HOURS": "16",
        "ODDS_API_IO_RELAXED_FUZZY_TOLERANCE_HOURS": "16",
        "ODDS_API_IO_RELAXED_MIN_SCORE": "42",
        # SStats context depth.
        "SSTATS_ENABLED": "true" if has_sstats else "false",
        "ENABLE_SSTATS": "true" if has_sstats else "false",
        "ENABLE_SSTATS_CONTEXT": "true" if has_sstats else "false",
        "SSTATS_CONTEXT_ENABLED": "true" if has_sstats else "false",
        "SSTATS_CONTEXT_MATCH_LIMIT": str(context_limit),
        "SSTATS_RECENT_MATCHES": "12",
        "SSTATS_LOOKBACK_DAYS": "60",
        "SSTATS_DEEP_ENDPOINTS_ENABLED": "true" if has_sstats else "false",
        "SSTATS_DEEP_REQUESTS_MAX_PER_RUN": str(sstats_deep if has_sstats else 0),
        "SSTATS_DEEP_DETAIL_LIMIT_PER_RUN": str(sstats_deep if has_sstats else 0),
        "SSTATS_DEEP_CONTEXT_MATCH_LIMIT": str(sstats_deep_context if has_sstats else 0),
        "SSTATS_DEEP_ENRICHMENT_ENABLED": "true" if has_sstats else "false",
        "SSTATS_DEEP_ENRICHMENT_AFTER_CROSSWALK": "true" if has_sstats else "false",
        "SSTATS_GAME_DETAIL_ENABLED": "true" if has_sstats else "false",
        "SSTATS_LAST_GAMES_STATS_ENABLED": "true" if has_sstats else "false",
        "SSTATS_GLICKO_ENABLED": "true" if has_sstats else "false",
        "SSTATS_ODDS_RESCUE_ENABLED": "true" if has_sstats else "false",
        "DAY_INVENTORY_SSTATS_MAX_REQUESTS": str(sstats if has_sstats else 0),
        # Bzzoiro context/odds overlap.  The previous live contract used small
        # pages/limits and actual runs often made only 4 calls.  Raise pages and
        # target limit so it can actually contribute context and secondary odds.
        "BZZOIRO_ENABLED": "true" if has_bzz else "false",
        "ENABLE_BZZOIRO": "true" if has_bzz else "false",
        "ENABLE_BZZOIRO_CONTEXT": "true" if has_bzz else "false",
        "BZZOIRO_CONTEXT_ENABLED": "true" if has_bzz else "false",
        "BZZOIRO_CONTEXT_MATCH_LIMIT": str(context_limit),
        "BZZOIRO_MAX_PAGES": str(bzz_pages),
        "BZZOIRO_PAGE_SIZE": str(bzz_page_size),
        "BZZOIRO_EVENTS_MAX_REQUESTS_PER_RUN": str(min(100, bzzoiro) if has_bzz else 0),
        "BZZOIRO_PREDICTIONS_MAX_REQUESTS_PER_RUN": str(min(90, bzzoiro) if has_bzz else 0),
        "BZZOIRO_PREDICTIONS_MAX_PAGES": "16",
        "BZZOIRO_V2_ENRICHMENT_ENABLED": "true" if has_bzz else "false",
        "BZZOIRO_V2_MATCH_LIMIT": str(premium_limit if has_bzz else 0),
        "BZZOIRO_V2_MAX_HTTP_REQUESTS_PER_RUN": str(bzzoiro if has_bzz else 0),
        "BZZOIRO_V2_EVENTS_ENABLED": "true" if has_bzz else "false",
        "BZZOIRO_V2_STATS_ENABLED": "true" if has_bzz else "false",
        "BZZOIRO_V2_METADATA_ENABLED": "true" if has_bzz else "false",
        "BZZOIRO_V2_LINEUPS_ENABLED": "true" if has_bzz else "false",
        "BZZOIRO_V2_ODDS_ENABLED": "true" if has_bzz else "false",
        "BZZOIRO_CURRENT_ODDS_AS_SECONDARY_SOURCE": "true",
        "BZZOIRO_ODDS_REKEY_ENABLED": "true",
        "BZZOIRO_ODDS_REKEY_MIN_SCORE": "60",
        "BZZOIRO_PRICE_BACKFILL_ENABLED": "true" if has_bzz else "false",
        "BZZOIRO_PRICE_BACKFILL_TARGET_LIMIT": str(min(160, premium_limit) if has_bzz else 0),
        "DAY_INVENTORY_BZZOIRO_MAX_REQUESTS": str(bzzoiro if has_bzz else 0),
        "DAY_INVENTORY_BZZOIRO_MAX_PAGES": str(bzz_pages),
        # Keep SportLogic disabled: in recent runs it consumed calls and returned
        # zero fixtures/offers.  This also fixes the misleading enabled=True report
        # caused by older quota layers overwriting the workflow env.
        "DAY_INVENTORY_ENABLE_SPORTLOGIC": "false",
        "SPORTLOGIC_ENABLED": "false",
        "ENABLE_SPORTLOGIC": "false",
        "SPORTLOGIC_CONTROLLED_ODDS_ENABLED": "false",
        "SPORTLOGIC_PER_RUN_MAX": "0",
        "SPORTLOGIC_MAX_HTTP_REQUESTS_PER_RUN": "0",
        "SPORTLOGIC_REQUESTS_MAX_PER_RUN": "0",
        "SPORTLOGIC_REQUEST_BUDGET_GRANTED": "0",
        "SPORTLOGIC_MATCH_LIMIT": "0",
        "SPORTLOGIC_CONTEXT_MATCH_LIMIT": "0",
        "SPORTLOGIC_ODDS_MATCH_LIMIT": "0",
    }
    _put_limit(env, "ODDS_API_IO", odds_total if has_odds_1 else 0)
    _put_limit(env, "SSTATS", sstats if has_sstats else 0)
    _put_limit(
        env,
        "BZZOIRO",
        bzzoiro if has_bzz else 0,
        "BZZOIRO_EVENTS_MAX_REQUESTS_PER_RUN",
        "BZZOIRO_PREDICTIONS_MAX_REQUESTS_PER_RUN",
    )
    _put_limit(env, "SPORTLOGIC", 0)

    _write_env(env)
    payload = {
        "status": "installed",
        "version": "v18-lines-context-uplift",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "phase": phase,
        "secrets_present": {
            "odds_api_io_key": has_odds_1,
            "odds_api_io_key_2": has_odds_2,
            "sstats": has_sstats,
            "bzzoiro": has_bzz,
        },
        "targets": {
            "inventory": 300,
            "context_backfill_limit": 300,
            "near_window_hours": 24,
            "odds_api_io_target_matches": 300,
            "sstats_context_match_limit": context_limit if has_sstats else 0,
            "sstats_deep_context_limit": sstats_deep_context if has_sstats else 0,
            "bzzoiro_context_match_limit": context_limit if has_bzz else 0,
            "bzzoiro_v2_match_limit": premium_limit if has_bzz else 0,
            "sportlogic": "disabled_by_env_zero_yield",
        },
        "env_written_count": len(env),
        "notes": [
            "Only data coverage/enrichment limits are raised; publication window, daily cap, xG, value and price guards stay strict.",
            "Odds-api.io request cap remains conservative; uplift is mostly target/pages/backfill driven.",
            "SportLogic is forced off because recent runs showed enabled=True with zero useful fixtures/offers.",
        ],
    }
    EXPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    EXPORT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
