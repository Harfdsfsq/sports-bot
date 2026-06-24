from __future__ import annotations

"""Apply the HARIZON A/B publication evidence contract.

Owner contract:
- A-tier: 2 odds/line confirmations, 2 bookmakers, 2 contexts.
- B-tier: 1 odds/line source, 1 bookmaker, 1 context.

The normal value/quality/price-integrity/line-movement/dedupe guards stay active.
"""

import json
import os
from datetime import datetime, timezone
from importlib import import_module
from pathlib import Path
from typing import Any

CONTRACT_ENV = {
    # Core A/B coverage contract.
    "PUBLISH_ALLOW_B_TIER": "true",
    "PUBLISH_COVERAGE_TIER_MODE": "hybrid",
    "PUBLISH_MIN_BOOKS": "1",
    "MIN_BOOKS_PUBLISH": "1",
    "PUBLISH_MIN_CONTEXT_SOURCES": "1",
    "MIN_CONTEXT_SOURCES_PUBLISH": "1",
    "PUBLISH_MIN_ODDS_SOURCES": "1",
    "MIN_SOURCES_PUBLISH": "1",
    "PUBLISH_TIER_A_MIN_ODDS_SOURCES": "2",
    "PUBLISH_TIER_A_MIN_BOOKS": "2",
    "PUBLISH_TIER_A_MIN_CONTEXT_SOURCES": "2",
    "PUBLISH_TIER_B_MIN_ODDS_SOURCES": "1",
    "PUBLISH_TIER_B_MIN_BOOKS": "1",
    "PUBLISH_TIER_B_MIN_CONTEXT_SOURCES": "1",

    # Main model candidate bridge. CandidateFactory previously kept too few
    # rows before final quality/value filters. On matches with many spread/total
    # buckets those slots were often consumed by market-simple rows, so real
    # totals/xG candidates never reached quality. This widens the pre-filter
    # shortlist; publication guards below are not relaxed.
    "MAX_CANDIDATES_PER_MATCH_PRE_FILTER": "14",
    "MAX_INTERNAL_CANDIDATES_PER_RUN": "24",
    "MAX_PICKS_PER_FAMILY": "6",
    "MAX_SAME_REASON_SIGNATURE": "6",
    "MAX_NON_CORE_PICKS_PER_RUN": "3",

    # Let A-cover/B-cover promotion seed the main CandidateFactory pool before
    # quality. This does not publish; main quality/value/xG/line/price guards
    # still decide whether a promoted row can become publishable.
    "MAIN_POOL_RESCUE_FILE_APPEND_ENABLED": "true",
    "MAIN_POOL_RESCUE_FILE_ALLOWED_SOURCES": "a_cover_market_promotion,b_cover_market_promotion",
    "MAIN_POOL_RESCUE_FILE_APPEND_LIMIT": "24",
    "PRE_RUN_A_COVER_PROMOTION_SEED_ENABLED": "true",

    # Quality-history stability. Do not let a tiny published-bet sample hard-stop
    # the entire current market/rescue pool. With fewer than 50 settled binary
    # bets, historical calibration/learning/segment guards stay informational;
    # value, xG, line movement, price-integrity, duplicate and publication score
    # guards still run.
    "QUALITY_MIN_HISTORY_BETS": "50",
    "CALIBRATION_MIN_SAMPLE": "24",
    "LEARNING_SCORE_MIN_SAMPLE": "24",
    "HISTORICAL_SEGMENT_MIN_SAMPLE": "24",
    "HISTORICAL_SEGMENT_HARD_MIN_BAD_SEGMENTS": "3",
    "CLV_QUALITY_MIN_SAMPLE": "18",

    # Controlled fallback follows the same owner contract.
    "CONTROLLED_FALLBACK_MIN_ODDS_SOURCES": "1",
    "CONTROLLED_FALLBACK_MIN_CONTEXT_SOURCES": "1",
    "CONTROLLED_FALLBACK_MIN_CONFIRMATION_SOURCES": "1",
    "CONTROLLED_FALLBACK_REQUIRE_2_BOOKS_FOR_TELEGRAM": "false",
    "CONTROLLED_FALLBACK_REQUIRE_2_ODDS_SOURCES_FOR_TELEGRAM": "false",
    "CONTROLLED_FALLBACK_REQUIRE_2_CONTEXT_SOURCES_FOR_TELEGRAM": "false",
    "CONTROLLED_FALLBACK_REQUIRE_INDEPENDENT_SOURCES": "false",
    "CONTROLLED_FALLBACK_TIER_A_REQUIRE_2_ODDS_SOURCES": "true",
    "CONTROLLED_FALLBACK_TIER_A_MIN_ODDS_SOURCES": "2",
    "CONTROLLED_FALLBACK_TIER_A_MIN_BOOKS": "2",
    "CONTROLLED_FALLBACK_TIER_A_MIN_CONTEXT_SOURCES": "2",
    "CONTROLLED_FALLBACK_TIER_B_REQUIRE_ODDS_SOURCES": "true",
    "CONTROLLED_FALLBACK_TIER_B_REQUIRE_INDEPENDENT_SOURCES": "false",
    "CONTROLLED_FALLBACK_TIER_B_MIN_ODDS_SOURCES": "1",
    "CONTROLLED_FALLBACK_TIER_B_MIN_BOOKS": "1",
    "CONTROLLED_FALLBACK_TIER_B_MIN_CONTEXT_SOURCES": "1",
    "CONTROLLED_FALLBACK_TIER_B_MIN_CONFIRMATION_SOURCES": "1",
    "CONTROLLED_FALLBACK_TIER_B_BOOKMAKER_QUORUM_PRICE_GUARD": "true",

    # Best-of-day allocator: keep one late slot, but release it from 17:00 MSK.
    # At 4/5 after 17:00 there is not enough day left to keep hiding viable
    # candidates behind the reserve reason; value/xG/quality/line guards still
    # decide whether the 5th pick is actually publishable.
    "CONTROLLED_FALLBACK_RESERVED_DAILY_SLOT_ENABLED": "true",
    "CONTROLLED_FALLBACK_RESERVED_DAILY_SLOTS": "1",
    "CONTROLLED_FALLBACK_RESERVED_SLOT_RELEASE_LOCAL_HOUR": "17",
    "CONTROLLED_FALLBACK_RESERVED_SLOT_RELEASE_LOCAL_MINUTE": "0",
    "CONTROLLED_FALLBACK_RESERVED_SLOT_ELITE_MIN_EV_PCT": "12.0",
    "CONTROLLED_FALLBACK_RESERVED_SLOT_ELITE_MIN_EDGE_PP": "6.5",
    "CONTROLLED_FALLBACK_RESERVED_SLOT_ELITE_MIN_CONFIDENCE": "73.0",
    "CONTROLLED_FALLBACK_RESERVED_SLOT_ELITE_MIN_QUALITY": "74.0",

    # V7: prevent unpublished/evaluated rows from blocking future review as
    # duplicate_match_market_selection_line. Published/sent/state indexes remain.
    "CONTROLLED_FALLBACK_STRICT_MATCH_MARKET_DEDUPE": "false",
    "CONTROLLED_FALLBACK_DEDUPE_SENT_INDEX_STRICT": "true",
    "CONTROLLED_FALLBACK_DEDUPE_PREVIOUS_REPORT": "true",

    # Keep publication quality strict enough for reserve mode.
    "CONTROLLED_FALLBACK_REQUIRE_TOTALS_SANITY_FOR_TELEGRAM": "true",
    "CONTROLLED_FALLBACK_TIER_B_MIN_CONFIDENCE": "68.0",
    "CONTROLLED_FALLBACK_TIER_B_MIN_QUALITY": "70.0",
    "CONTROLLED_FALLBACK_TIER_B_MIN_EDGE_PP": "5.0",
    "CONTROLLED_FALLBACK_TIER_B_MIN_EV_PCT": "8.0",
    "CONTROLLED_FALLBACK_TIER_B_MIN_PUBLICATION_SCORE": "20.0",
    "CONTROLLED_FALLBACK_FINAL_MIN_EDGE_PP": "5.0",
    "CONTROLLED_FALLBACK_FINAL_MIN_EV_PCT": "8.0",
    "CONTROLLED_FALLBACK_PROXY_SINGLE_SOURCE_STRICT": "true",
    "CONTROLLED_FALLBACK_PROXY_SINGLE_SOURCE_MIN_EDGE_PP": "5.0",
    "CONTROLLED_FALLBACK_PROXY_SINGLE_SOURCE_MIN_EV_PCT": "8.0",
    "CONTROLLED_FALLBACK_PROXY_SINGLE_SOURCE_MIN_CONFIDENCE": "70.0",

    # Keep promotion focused on candidates that could realistically survive the
    # final B-tier value bar, while still producing useful diagnostics.
    "PROMOTE_B_COVER_MIN_EDGE_PP": "2.0",
    "PROMOTE_B_COVER_MIN_EV_PCT": "4.0",
    "PROMOTE_B_COVER_VALUE_CANDIDATE_LIMIT": "36",
}


def _append_github_env(values: dict[str, str]) -> None:
    path = os.getenv("GITHUB_ENV")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as fh:
        for key, value in values.items():
            fh.write(f"{key}={value}\n")


def _truthy(value: Any, default: bool = False) -> bool:
    raw = str(value if value is not None else "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on", "force"}


def _run_optional(module_path: str, func_name: str = "main") -> dict[str, Any]:
    try:
        module = import_module(module_path)
        func = getattr(module, func_name, None)
        if callable(func):
            result = func()
        else:
            result = None
        return {"status": "ok", "module": module_path, "result": result}
    except Exception as exc:
        return {"status": "error_ignored", "module": module_path, "error": f"{type(exc).__name__}: {exc}"}


def _preseed_main_pool_rescue() -> dict[str, Any]:
    if not _truthy(os.getenv("PRE_RUN_A_COVER_PROMOTION_SEED_ENABLED"), True):
        return {"enabled": False, "reason": "disabled"}
    steps = [
        _run_optional("scripts.build_context_source_index"),
        _run_optional("scripts.build_b_cover_candidate_gap_report"),
        _run_optional("scripts.promote_a_cover_value_candidates"),
        _run_optional("scripts.enrich_rescue_candidates_xg_confirmation"),
    ]
    rescue_path = Path(".data/exports/latest-rescue-candidates.json")
    count = 0
    try:
        payload = json.loads(rescue_path.read_text(encoding="utf-8")) if rescue_path.exists() else []
        count = len(payload) if isinstance(payload, list) else 0
    except Exception:
        count = 0
    return {"enabled": True, "steps": steps, "rescue_candidates": count, "rescue_path": str(rescue_path)}


def main() -> int:
    os.environ.update(CONTRACT_ENV)
    _append_github_env(CONTRACT_ENV)
    out = Path(".data/exports/latest-ab-tier-bookmaker-contract-policy.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    preseed = _preseed_main_pool_rescue()
    payload = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "applied",
        "contract": {
            "A": {"min_odds_sources": 2, "min_bookmakers": 2, "min_context_sources": 2},
            "B": {"min_odds_sources": 1, "min_bookmakers": 1, "min_context_sources": 1},
            "model_candidate_bridge": {
                "max_candidates_per_match_pre_filter": int(CONTRACT_ENV["MAX_CANDIDATES_PER_MATCH_PRE_FILTER"]),
                "max_internal_candidates_per_run": int(CONTRACT_ENV["MAX_INTERNAL_CANDIDATES_PER_RUN"]),
                "main_pool_rescue_file_append": True,
                "pre_run_a_cover_promotion_seed": preseed,
                "purpose": "let totals/xG and promoted A-cover candidates reach quality filters before final guards",
            },
            "quality_history_stability": {
                "min_settled_binary_bets_for_historical_hard_guards": int(CONTRACT_ENV["QUALITY_MIN_HISTORY_BETS"]),
                "calibration_min_sample": int(CONTRACT_ENV["CALIBRATION_MIN_SAMPLE"]),
                "learning_score_min_sample": int(CONTRACT_ENV["LEARNING_SCORE_MIN_SAMPLE"]),
                "historical_segment_min_sample": int(CONTRACT_ENV["HISTORICAL_SEGMENT_MIN_SAMPLE"]),
                "purpose": "avoid hard rejection from underpowered historical segments while keeping live guards active",
            },
            "daily_allocator": {
                "reserved_slots": 1,
                "release_local_time": "17:00",
                "elite_override": {"min_ev_pct": 12.0, "min_edge_pp": 6.5, "min_confidence": 73.0, "min_quality": 74.0},
            },
            "independent_odds_sources": "required_for_a_tier_only",
            "guards_unchanged": ["value", "xg", "quality_score", "line_movement", "price_integrity", "dedupe", "daily_limit", "publish_window"],
        },
        "env": CONTRACT_ENV,
    }
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print("Applied HARIZON A/B publication contract: A=2 odds/2 books/2 context, B=1 odds/1 book/1 context; model prefilter widened; stable-history quality policy enabled; pre-run A-cover rescue seed enabled; reserved daily slot releases at 17:00 local")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
