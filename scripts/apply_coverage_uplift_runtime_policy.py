from __future__ import annotations

"""Runtime coverage uplift for HARIZON.

This writes data-collection settings to GitHub Actions env.  It is intentionally
about coverage only: final candidates still need valid odds, bookmaker quorum,
context, line lifecycle, positive value and Telegram safety guards.
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(".").resolve()
EXPORT_PATH = ROOT / ".data" / "exports" / "latest-coverage-uplift-runtime-policy.json"
GITHUB_ENV = os.getenv("GITHUB_ENV")
VERSION = "v21-full-inventory-targeted-coverage"


def truthy(value: object, default: bool = False) -> bool:
    raw = str(value if value is not None else "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on", "force"}


def present(*names: str) -> bool:
    return any(str(os.getenv(name) or "").strip() for name in names)


def phase() -> str:
    explicit = str(os.getenv("HARIZON_RUN_PHASE") or os.getenv("RUN_PHASE") or "").strip().lower()
    if explicit:
        return explicit
    try:
        tz = ZoneInfo(os.getenv("APP_TIMEZONE") or os.getenv("TZ") or "Europe/Moscow")
    except Exception:
        tz = timezone.utc
    hour = datetime.now(timezone.utc).astimezone(tz).hour
    if 0 <= hour <= 2:
        return "full_inventory"
    if 3 <= hour <= 10:
        return "morning_backfill"
    return "live_refresh"


def put_limit(env: dict[str, str], prefix: str, value: int, *extra_aliases: str) -> None:
    upper = prefix.upper()
    for key in {
        f"{upper}_PER_RUN_MAX",
        f"{upper}_MAX_HTTP_REQUESTS_PER_RUN",
        f"{upper}_MAX_REQUESTS_PER_RUN",
        f"{upper}_REQUESTS_MAX_PER_RUN",
        f"{upper}_REQUEST_BUDGET_GRANTED",
        *extra_aliases,
    }:
        env[key] = str(max(0, int(value)))


def write_env(env: dict[str, str]) -> None:
    for key, value in env.items():
        os.environ[str(key)] = str(value)
    if GITHUB_ENV:
        with open(GITHUB_ENV, "a", encoding="utf-8") as fh:
            for key in sorted(env):
                fh.write(f"{key}={env[key]}\n")
    else:
        for key in sorted(env):
            print(f"{key}={env[key]}")


def report(payload: dict) -> None:
    EXPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    EXPORT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def main() -> int:
    run_phase = phase()
    if not truthy(os.getenv("HARIZON_COVERAGE_UPLIFT_ENABLED"), True):
        report({"status": "disabled_by_env", "version": VERSION, "phase": run_phase, "created_at_utc": datetime.now(timezone.utc).isoformat()})
        return 0

    # All phases now target the whole 300-row inventory.  Earlier live runs only
    # handed Bzzoiro about 36 runner-window matches and kept SStats deep at 40,
    # which left most inventory rows unexplained.  These are per-run ceilings; the
    # provider budget/HTTP guards still stop on 429/5xx.
    if run_phase == "full_inventory":
        odds_total, odds_account, sstats_budget, bzz_budget = 100, 50, 150, 260
        context_limit, premium_limit, sportlogic_budget = 300, 300, 8
        bzz_pages, bzz_page_size = 30, 40
        sstats_deep_budget = 140
    elif run_phase == "morning_backfill":
        odds_total, odds_account, sstats_budget, bzz_budget = 100, 50, 150, 240
        context_limit, premium_limit, sportlogic_budget = 300, 300, 7
        bzz_pages, bzz_page_size = 28, 40
        sstats_deep_budget = 130
    else:
        odds_total, odds_account, sstats_budget, bzz_budget = 100, 50, 150, 220
        context_limit, premium_limit, sportlogic_budget = 300, 300, 6
        bzz_pages, bzz_page_size = 26, 40
        sstats_deep_budget = 120

    has_odds_1 = present("ODDS_API_IO_KEY")
    has_odds_2 = present("ODDS_API_IO_KEY_2", "ODDS_API_IO_KEY2", "ODDS_API_IO_ACC2_KEY", "ODDS_API_IO_SECONDARY_KEY")
    has_sstats = present("SSTATS_API_KEY")
    has_bzz = present("BZZOIRO_API_KEY")
    has_sportlogic = present("SPORTLOGIC_API_KEY", "SPORTLOGIC_KEY", "SPORTLOGIC_TOKEN")

    env = {
        "HARIZON_COVERAGE_UPLIFT_VERSION": VERSION,
        "HARIZON_COVERAGE_UPLIFT_PHASE": run_phase,
        "HARIZON_COVERAGE_UPLIFT_ENABLED": "true",
        "RUN_MODE": "normal",
        "PUBLISH_WINDOW_HOURS": "2",
        "CONTROLLED_FALLBACK_PUBLISH_WINDOW_HOURS": "2",
        "CONTROLLED_FALLBACK_DAILY_MAX_PUBLISHED": os.getenv("CONTROLLED_FALLBACK_DAILY_MAX_PUBLISHED") or "3",
        "CONTROLLED_FALLBACK_DAILY_MAX_B_TIER": os.getenv("CONTROLLED_FALLBACK_DAILY_MAX_B_TIER") or "3",
        "CONTROLLED_FALLBACK_DAILY_LIMIT_TIMEZONE": os.getenv("CONTROLLED_FALLBACK_DAILY_LIMIT_TIMEZONE") or "Europe/Moscow",
        "CONTROLLED_FALLBACK_DAILY_LIMIT_USE_PUBLISHED_AT": "true",
        "DAY_INVENTORY_TARGET_SIZE": "300",
        "DAY_INVENTORY_MAX_MATCHES": "300",
        "DAY_INVENTORY_FORCE_FULL_300": "true",
        "DAY_INVENTORY_FORCE_TOP_300": "true",
        "DAY_INVENTORY_FORCE_PROVIDER_MERGE": "true",
        "DAY_INVENTORY_USE_FOR_RUN": "true",
        "DAY_INVENTORY_SEMANTIC_DEDUPE_ENABLED": "true",
        "DAY_INVENTORY_HORIZON_DAYS": "2",
        "DAY_INVENTORY_TARGET_HORIZON_DAYS": "2",
        "RUN_DAYS_AHEAD": "2",
        "DAY_INVENTORY_CONTEXT_BACKFILL_ENABLED": "true",
        "DAY_INVENTORY_CONTEXT_BACKFILL_LIMIT": "300",
        "CONTEXT_ENRICHMENT_REQUIRES_OFFERS": "false",
        "CONTEXT_ENRICHMENT_MATCH_LIMIT": str(context_limit),
        "PREMIUM_CONTEXT_SHORTLIST_LIMIT": str(premium_limit),
        "MAX_MATCHES_FOR_ODDS_FETCH": "300",
        "LINE_MOVEMENT_MIN_CURRENT_EV_PCT": os.getenv("LINE_MOVEMENT_MIN_CURRENT_EV_PCT") or "2.85",
        "LINE_MOVEMENT_MIN_CURRENT_EDGE_PP": os.getenv("LINE_MOVEMENT_MIN_CURRENT_EDGE_PP") or "1.45",
        "DAY_INVENTORY_ODDS_API_IO_TARGET_MATCHES": "300",
        "DAY_INVENTORY_MULTI_SOURCE_MAX_MATCHES": "300",
        "ODDS_API_IO_MAX_EVENTS": "100",
        "ODDS_API_IO_MAX_ODDS_EVENTS_PER_RUN": "100",
        "ODDS_API_IO_MAX_EVENT_PAGES_PER_SPORT": "24",
        "ODDS_API_IO_MAX_PAGES_PER_SPORT": "24",
        "ODDS_API_IO_PAGE_LIMIT": "100",
        "ODDS_API_IO_ACCOUNT1_PER_RUN_MAX": str(odds_account if has_odds_1 else 0),
        "ODDS_API_IO_ACCOUNT2_PER_RUN_MAX": str(odds_account if has_odds_2 else 0),
        "PRICE_BACKFILL_ODDS_API_IO_BATCHES_PER_ACCOUNT": "8",
        "PRICE_BACKFILL_ODDS_API_IO_MAX_EVENT_IDS_PER_REQUEST": "12",
        "PRICE_BACKFILL_ODDS_API_IO_EVENT_LIMIT": "220",
        "ODDS_API_IO_RELAXED_INVENTORY_MATCHING_ENABLED": "true",
        "ODDS_API_IO_RELAXED_EXACT_TOLERANCE_HOURS": "16",
        "ODDS_API_IO_RELAXED_FUZZY_TOLERANCE_HOURS": "16",
        "SSTATS_ENABLED": "true" if has_sstats else "false",
        "ENABLE_SSTATS": "true" if has_sstats else "false",
        "ENABLE_SSTATS_CONTEXT": "true" if has_sstats else "false",
        "SSTATS_CONTEXT_ENABLED": "true" if has_sstats else "false",
        "SSTATS_CONTEXT_MATCH_LIMIT": str(context_limit if has_sstats else 0),
        "SSTATS_RECENT_MATCHES": "12",
        "SSTATS_FORM_MIN_SAMPLE_PER_TEAM": "2",
        "SSTATS_LOOKBACK_DAYS": "60",
        "SSTATS_REQUEST_CHUNK_DAYS": "7",
        "SSTATS_DEEP_ENRICHMENT_ENABLED": "true" if has_sstats else "false",
        "SSTATS_GAME_DETAIL_ENABLED": "true" if has_sstats else "false",
        "SSTATS_LAST_GAMES_STATS_ENABLED": "true" if has_sstats else "false",
        "SSTATS_GLICKO_ENABLED": "true" if has_sstats else "false",
        "SSTATS_DEEP_DETAIL_LIMIT_PER_RUN": str(sstats_deep_budget if has_sstats else 0),
        "SSTATS_DEEP_CONTEXT_MATCH_LIMIT": str(premium_limit if has_sstats else 0),
        "SSTATS_DEEP_REQUESTS_MAX_PER_RUN": str(sstats_deep_budget if has_sstats else 0),
        "SSTATS_DEEP_MAX_REQUESTS_PER_RUN": str(sstats_deep_budget if has_sstats else 0),
        "DAY_INVENTORY_SSTATS_MAX_REQUESTS": str(sstats_budget if has_sstats else 0),
        "BZZOIRO_ENABLED": "true" if has_bzz else "false",
        "ENABLE_BZZOIRO": "true" if has_bzz else "false",
        "ENABLE_BZZOIRO_CONTEXT": "true" if has_bzz else "false",
        "BZZOIRO_CONTEXT_ENABLED": "true" if has_bzz else "false",
        "BZZOIRO_CONTEXT_MATCH_LIMIT": str(context_limit if has_bzz else 0),
        "BZZOIRO_ODDS_MATCH_LIMIT": str(premium_limit if has_bzz else 0),
        "BZZOIRO_MAX_PAGES": str(bzz_pages),
        "BZZOIRO_PAGE_SIZE": str(bzz_page_size),
        "BZZOIRO_V2_ENRICHMENT_ENABLED": "true" if has_bzz else "false",
        "BZZOIRO_V2_MATCH_LIMIT": str(premium_limit if has_bzz else 0),
        "BZZOIRO_V2_SOURCE_MATRIX_TARGETS_ENABLED": "true" if has_bzz else "false",
        "BZZOIRO_V2_SOURCE_MATRIX_TARGET_LIMIT": str(premium_limit if has_bzz else 0),
        "BZZOIRO_CONTEXT_GAP_MATCH_LIMIT": str(premium_limit if has_bzz else 0),
        "BZZOIRO_V2_ODDS_ENABLED": "true" if has_bzz else "false",
        "BZZOIRO_V2_FETCH_EVENT_ODDS": "true" if has_bzz else "false",
        "BZZOIRO_V2_FETCH_EVENT_STATS": "true" if has_bzz else "false",
        "BZZOIRO_V2_FETCH_EVENT_METADATA": "true" if has_bzz else "false",
        "BZZOIRO_V2_FETCH_EVENT_PREDICTION": "true" if has_bzz else "false",
        "BZZOIRO_V2_FETCH_ODDS_COMPARISON": "true" if has_bzz else "false",
        "BZZOIRO_V2_ODDS_COMPARISON_MATCH_LIMIT": "120" if has_bzz else "0",
        "BZZOIRO_V2_ODDS_COMPARISON_MAX_REQUESTS": "120" if has_bzz else "0",
        "BZZOIRO_CURRENT_ODDS_AS_SECONDARY_SOURCE": "true" if has_bzz else "false",
        "BZZOIRO_ODDS_REKEY_ENABLED": "true" if has_bzz else "false",
        "BZZOIRO_PRICE_BACKFILL_ENABLED": "true" if has_bzz else "false",
        "DAY_INVENTORY_BZZOIRO_MAX_REQUESTS": str(bzz_budget if has_bzz else 0),
        "DAY_INVENTORY_BZZOIRO_MAX_PAGES": str(bzz_pages),
        "DAY_INVENTORY_ENABLE_SPORTLOGIC": "true" if has_sportlogic else "false",
        "SPORTLOGIC_ENABLED": "true" if has_sportlogic else "false",
        "ENABLE_SPORTLOGIC": "true" if has_sportlogic else "false",
        "SPORTLOGIC_CONTROLLED_ODDS_ENABLED": "true" if has_sportlogic else "false",
        "SPORTLOGIC_MATCH_LIMIT": "80" if has_sportlogic else "0",
        "SPORTLOGIC_CONTEXT_MATCH_LIMIT": "80" if has_sportlogic else "0",
        "SPORTLOGIC_ODDS_MATCH_LIMIT": "24" if has_sportlogic else "0",
        "DAY_INVENTORY_SPORTLOGIC_MATCH_LIMIT": "80" if has_sportlogic else "0",
        "DAY_INVENTORY_SPORTLOGIC_MAX_REQUESTS": str(sportlogic_budget if has_sportlogic else 0),
        "SPORTLOGIC_ACTIVE_ODDS_TARGETED_CONFIRMATION_ENABLED": "true" if has_sportlogic else "false",
        "SPORTLOGIC_ACTIVE_ODDS_ALLOW_WITHOUT_CURRENT_GAMES": "true" if has_sportlogic else "false",
        "SPORTLOGIC_SKIP_ACTIVE_ODDS_WHEN_NO_CURRENT_GAMES": "false" if has_sportlogic else "true",
        "SPORTLOGIC_TARGETED_GAME_DETAIL_LIMIT": "4" if has_sportlogic else "0",
        "SPORTLOGIC_MATCH_MIN_SCORE": "48",
    }
    put_limit(env, "ODDS_API_IO", odds_total if has_odds_1 else 0)
    put_limit(env, "SSTATS", sstats_budget if has_sstats else 0)
    put_limit(env, "BZZOIRO", bzz_budget if has_bzz else 0, "BZZOIRO_EVENTS_MAX_REQUESTS_PER_RUN", "BZZOIRO_PREDICTIONS_MAX_REQUESTS_PER_RUN")
    put_limit(env, "SPORTLOGIC", sportlogic_budget if has_sportlogic else 0)

    write_env(env)
    report({
        "status": "installed",
        "version": VERSION,
        "phase": run_phase,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "providers_enabled": {"odds_api_io": has_odds_1, "odds_api_io_2": has_odds_2, "sstats": has_sstats, "bzzoiro": has_bzz, "sportlogic": has_sportlogic},
        "targets": {"inventory": 300, "horizon_days": 2, "context_limit": context_limit, "sstats_deep_budget": sstats_deep_budget, "bzzoiro_v2_limit": premium_limit, "bzzoiro_comparison_limit": env["BZZOIRO_V2_ODDS_COMPARISON_MATCH_LIMIT"], "sportlogic_budget": sportlogic_budget if has_sportlogic else 0, "line_ev_floor": env["LINE_MOVEMENT_MIN_CURRENT_EV_PCT"], "line_edge_floor": env["LINE_MOVEMENT_MIN_CURRENT_EDGE_PP"]},
        "notes": [
            "All phases target the full semantic 300-row inventory instead of only the 2h runner window.",
            "SStats deep limits are now explicit; workflow defaults cannot silently hold them at 40.",
            "Bzzoiro v2 source-matrix and odds-comparison target limits are raised to 300/120.",
        ],
        "env_written_count": len(env),
    })
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
