from __future__ import annotations

"""Runtime contract for the Focused Alpha production architecture.

The policy changes the optimisation target, not the safety standard.  It reduces
provider scope and publication volume, keeps a broad data window, and requires the
strict A contract for live Telegram output.  Shadow candidates remain available for
future calibration.
"""

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / ".data" / "exports" / "latest-focused-alpha-runtime-policy.json"

POLICY: dict[str, str] = {
    "FOCUSED_ALPHA_ENABLED": "true",
    "FOCUSED_ALPHA_MAX_MATCHES": "100",
    "FOCUSED_ALPHA_PHASE_TARGETS": "40,70,100",
    "FOCUSED_ALPHA_MIN_MATCH_SCORE": "44",
    "FOCUSED_ALPHA_MAX_PER_LEAGUE": "10",
    "FOCUSED_ALPHA_EXPLORATION_SLOTS": "6",
    "FOCUSED_ALPHA_DAILY_MAX_DECISIONS": "2",
    "FOCUSED_ALPHA_LIVE_ENABLED": "false",
    "FOCUSED_ALPHA_MIN_CONSERVATIVE_EV_PCT": "2.0",
    "FOCUSED_ALPHA_MIN_EDGE_PP": "2.0",
    "FOCUSED_ALPHA_MIN_QUALITY": "68",
    "FOCUSED_ALPHA_MIN_CONFIDENCE": "68",
    # Collect early, publish late.  app.cli disables main publication when the data
    # window is wider than the final controlled-fallback window.
    "HARIZON_DATA_COLLECTION_WINDOW_HOURS": "36",
    "HARIZON_DISABLE_MAIN_PUBLICATION_FOR_DATA_WINDOW": "true",
    "PUBLISH_WINDOW_HOURS": "2",
    "CONTROLLED_FALLBACK_PUBLISH_WINDOW_HOURS": "2",
    "MIN_KICKOFF_LEAD_MINUTES": "20",
    # No volume target.  At most two distinct high-quality decisions per day.
    "MAX_PICKS_PER_RUN": "2",
    "CONTROLLED_FALLBACK_DAILY_MAX_PUBLISHED": "2",
    "CONTROLLED_FALLBACK_DAILY_MAX_B_TIER": "2",
    "REPUBLISH_SEEN_CANDIDATES_WHEN_EMPTY": "false",
    "BANKROLL_FORCE_MIN_STAKE_WHEN_EMPTY_ENABLED": "false",
    # Live output is A-only until the canonical history has sufficient probability,
    # closing-price and settlement coverage.  B remains useful as a watchlist.
    "PUBLISH_ALLOW_B_TIER": "false",
    "PUBLISH_B_TIER_WATCH_ONLY": "true",
    "PUBLISH_COVERAGE_TIER_MODE": "a_only_publish_b_watchlist",
    "CONTROLLED_FALLBACK_TELEGRAM_ALLOW_TIER_B": "false",
    "CONTROLLED_FALLBACK_TIER_B_PUBLISH_ENABLED": "false",
    "CONTROLLED_FALLBACK_TIER_B_WATCH_ONLY": "true",
    # Exact provider identity, bookmaker quorum and hard context remain mandatory.
    "MIN_BOOKS_PUBLISH": "2",
    "PUBLISH_MIN_BOOKS": "2",
    "MIN_SOURCES_PUBLISH": "2",
    "PUBLISH_MIN_ODDS_SOURCES": "2",
    "PUBLISH_MIN_CONTEXT_SOURCES": "2",
    "MIN_CONTEXT_SOURCES_PUBLISH": "2",
    "CONTROLLED_FALLBACK_MIN_ODDS_SOURCES": "2",
    "CONTROLLED_FALLBACK_MIN_CONTEXT_SOURCES": "2",
    "CONTROLLED_FALLBACK_MIN_CONFIRMATION_SOURCES": "2",
    "CONTROLLED_FALLBACK_TIER_A_REQUIRE_2_ODDS_SOURCES": "true",
    "CONTROLLED_FALLBACK_TIER_A_MIN_ODDS_SOURCES": "2",
    "CONTROLLED_FALLBACK_TIER_A_MIN_BOOKS": "2",
    "CONTROLLED_FALLBACK_TIER_A_MIN_CONTEXT_SOURCES": "2",
    "CONTROLLED_FALLBACK_TIER_A_MIN_CONFIRMATION_SOURCES": "2",
    "CONTROLLED_FALLBACK_TIER_A_REQUIRE_RAW_QUALITY": "true",
    "CONTROLLED_FALLBACK_USE_QUALITY_PROXY": "false",
    "CONTROLLED_FALLBACK_REQUIRE_2_BOOKS_FOR_TELEGRAM": "true",
    "CONTROLLED_FALLBACK_REQUIRE_INDEPENDENT_SOURCES": "true",
    "CONTROLLED_FALLBACK_REQUIRE_TOTALS_SANITY_FOR_TELEGRAM": "true",
    "CONTROLLED_FALLBACK_REQUIRE_LINE_RECHECK": "true",
    "CONTROLLED_FALLBACK_REQUIRE_FINAL_CRON_RECHECK": "true",
    "LINE_MOVEMENT_GUARD_ENABLED": "true",
    "LINE_MOVEMENT_MIN_SNAPSHOTS": "2",
    "ODDS_SOURCE_INDEPENDENCE_ENABLED": "true",
    "BOOKMAKER_QUORUM_ENABLED": "true",
}


def _enabled() -> bool:
    return str(os.getenv("FOCUSED_ALPHA_POLICY_ENABLED", "true")).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
        "force",
    }


def _write(payload: dict[str, Any]) -> None:
    try:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except Exception:
        pass


def apply(*, force: bool = True) -> dict[str, Any]:
    if not _enabled():
        payload = {"status": "disabled", "publication_contract_relaxed": False}
        _write(payload)
        return payload
    before = {key: os.getenv(key) for key in POLICY}
    for key, value in POLICY.items():
        if force or os.getenv(key) in (None, ""):
            os.environ[key] = value
    after = {key: os.getenv(key) for key in POLICY}
    changed = {key: {"before": before[key], "after": after[key]} for key in POLICY if before[key] != after[key]}
    payload = {
        "status": "applied",
        "created_at_utc": datetime.now(UTC).isoformat(),
        "mode": "focused_alpha_a_only_live_b_shadow",
        "force_operator_contract": force,
        "changed": changed,
        "effective": after,
        "publication_minimum_count": 0,
        "daily_max_published": 2,
        "provider_focus_max_matches": 100,
        "main_publication_disabled_for_wide_data_window": True,
        "live_learning_auto_tuning": False,
        "publication_contract_relaxed": False,
    }
    _write(payload)
    return payload


__all__ = ["POLICY", "apply"]
