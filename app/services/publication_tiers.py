from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from app.services.coverage_contract import sync_candidate_publish_coverage
from app.services.line_movement_state import evaluate_and_record_line_movement

UTC = timezone.utc


@dataclass(frozen=True)
class PublicationTierDecision:
    passed: bool
    tier: str
    reasons: list[str]
    report: dict[str, Any]


def _as_int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ""):
            return default
        return int(float(str(value)))
    except Exception:
        return default


def _get_count(report: dict[str, Any], key: str, fallback: int = 0) -> int:
    return _as_int(report.get(key), fallback)


def classify_publication_tier(candidate: Any, settings: Any, *, now: datetime | None = None) -> PublicationTierDecision:
    now = (now or datetime.now(UTC)).astimezone(UTC)
    coverage = sync_candidate_publish_coverage(candidate, settings)
    report = dict(coverage.report)
    odds_count = _get_count(report, "odds_sources_count", _as_int(getattr(candidate, "sources_count", 0), 0))
    context_count = _get_count(report, "context_sources_count", 0)
    books_count = _get_count(report, "books_count", _as_int(getattr(candidate, "books_count", 0), 0))
    price_count = max(
        _get_count(report, "price_sources_count", 0),
        _get_count(report, "line_sources_count", 0),
        books_count,
        _as_int(getattr(candidate, "price_sources_count", 0), 0),
    )
    # A-tier is strict independent-provider confirmation. B-tier is a safer
    # reserve tier: 1+ line/price confirmation + 1+ context source, but only
    # after line movement is confirmed on a later run.
    line_count_for_tier_b = max(odds_count, price_count)

    tier_a_odds = max(2, _as_int(os.getenv("PUBLISH_TIER_A_MIN_ODDS_SOURCES"), 2))
    tier_a_context = max(2, _as_int(os.getenv("PUBLISH_TIER_A_MIN_CONTEXT_SOURCES"), 2))
    tier_b_odds = max(1, _as_int(os.getenv("PUBLISH_TIER_B_MIN_ODDS_SOURCES"), 1))
    tier_b_context = max(1, _as_int(os.getenv("PUBLISH_TIER_B_MIN_CONTEXT_SOURCES"), 1))

    movement = evaluate_and_record_line_movement(candidate, settings, now=now)
    report["line_movement"] = movement
    report["tier_thresholds"] = {
        "A": {"min_independent_odds_sources": tier_a_odds, "min_context_sources": tier_a_context},
        "B": {"min_line_sources": tier_b_odds, "min_context_sources": tier_b_context, "requires_movement_confirmed": True},
    }
    report["line_sources_count_for_tier_b"] = line_count_for_tier_b
    report["price_sources_count"] = price_count
    report["found_value"] = True

    is_a = odds_count >= tier_a_odds and context_count >= tier_a_context
    is_b = line_count_for_tier_b >= tier_b_odds and context_count >= tier_b_context
    movement_status = str(movement.get("status") or "")
    reasons: list[str] = []

    if is_a and movement_status in {"movement_confirmed", "publish_now_no_next_cron"} and bool(movement.get("passed")):
        tier = "A"
        passed = True
    elif is_b and movement_status == "movement_confirmed" and bool(movement.get("passed")):
        tier = "B"
        passed = True
    else:
        tier = "A_wait" if is_a else "B_wait" if is_b else "blocked"
        passed = False
        if not is_b:
            reasons.append(
                f"insufficient_tier_b_sources:lines={line_count_for_tier_b}/{tier_b_odds};context={context_count}/{tier_b_context}"
            )
        elif is_a and movement_status not in {"movement_confirmed", "publish_now_no_next_cron"}:
            reasons.append(f"tier_a_line_movement_not_ready:{movement_status}")
        elif is_b and movement_status != "movement_confirmed":
            reasons.append(f"tier_b_line_movement_not_confirmed:{movement_status}")
        if movement.get("reasons"):
            reasons.extend(str(item) for item in movement.get("reasons") or [])
        if not reasons and not movement.get("passed"):
            reasons.append(f"line_movement_not_ready:{movement_status}")

    report["publication_tier"] = tier
    report["publication_tier_passed"] = passed
    report["odds_sources_count"] = odds_count
    report["context_sources_count"] = context_count
    report["books_count"] = books_count
    report["can_publish"] = passed
    report["found_value_but_blocked"] = bool(not passed)
    return PublicationTierDecision(passed=passed, tier=tier, reasons=reasons, report=report)
