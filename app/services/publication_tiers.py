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
        return int(float(str(value).replace(",", ".")))
    except Exception:
        return default


def _get_count(report: dict[str, Any], key: str, fallback: int = 0) -> int:
    return _as_int(report.get(key), fallback)


def _env_int(name: str, default: int, minimum: int = 0) -> int:
    return max(minimum, _as_int(os.getenv(name), default))


def classify_publication_tier(candidate: Any, settings: Any, *, now: datetime | None = None) -> PublicationTierDecision:
    """Classify a candidate by the HARIZON A/B publication contract.

    Project contract requested by the owner:
    * B-tier: 1+ bookmaker/price confirmation + 1+ context source;
    * A-tier: 2+ bookmakers/price confirmations + 2+ context sources.

    Independent odds-source count remains diagnostic only.  It must not block
    a valid bookmaker-confirmed B-tier or A-tier candidate.  Value, xG/quality
    and line-movement guards still run after this coverage classification.
    """

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

    # User-facing contract is bookmaker/context based.  Keep odds_count in the
    # report as diagnostics, but gate tiers by bookmaker/price confirmations.
    price_or_bookmaker_count = max(books_count, price_count)

    tier_a_books = _env_int("PUBLISH_TIER_A_MIN_BOOKS", 2, 2)
    tier_a_context = _env_int("PUBLISH_TIER_A_MIN_CONTEXT_SOURCES", 2, 2)
    tier_b_books = _env_int("PUBLISH_TIER_B_MIN_BOOKS", _as_int(os.getenv("PUBLISH_MIN_BOOKS"), 1), 1)
    tier_b_context = _env_int("PUBLISH_TIER_B_MIN_CONTEXT_SOURCES", _as_int(os.getenv("PUBLISH_MIN_CONTEXT_SOURCES"), 1), 1)

    movement = evaluate_and_record_line_movement(candidate, settings, now=now)
    report["line_movement"] = movement
    report["tier_thresholds"] = {
        "A": {
            "min_bookmakers_or_price_confirmations": tier_a_books,
            "min_context_sources": tier_a_context,
            "independent_odds_sources_are_diagnostic_only": True,
        },
        "B": {
            "min_bookmakers_or_price_confirmations": tier_b_books,
            "min_context_sources": tier_b_context,
            "requires_movement_confirmed": True,
            "independent_odds_sources_are_diagnostic_only": True,
        },
    }
    report["price_sources_count"] = price_count
    report["bookmakers_or_price_confirmations_count"] = price_or_bookmaker_count
    report["found_value"] = True

    is_a = price_or_bookmaker_count >= tier_a_books and context_count >= tier_a_context
    is_b = price_or_bookmaker_count >= tier_b_books and context_count >= tier_b_context
    movement_status = str(movement.get("status") or "")
    movement_ready = movement_status in {"movement_confirmed", "publish_now_no_next_cron"} and bool(movement.get("passed"))
    reasons: list[str] = []

    if is_a and movement_ready:
        tier = "A"
        passed = True
    elif is_b and movement_ready:
        tier = "B"
        passed = True
    else:
        tier = "A_wait" if is_a else "B_wait" if is_b else "blocked"
        passed = False
        if not is_b:
            reasons.append(
                "insufficient_tier_b_coverage:"
                f"bookmakers={price_or_bookmaker_count}/{tier_b_books};"
                f"context={context_count}/{tier_b_context}"
            )
        elif is_a and not movement_ready:
            reasons.append(f"tier_a_line_movement_not_ready:{movement_status}")
        elif is_b and not movement_ready:
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
    report["tier_a_bookmaker_context_quorum_passed"] = is_a
    report["tier_b_bookmaker_context_quorum_passed"] = is_b
    report["tier_confirmation_mode"] = "bookmaker_context_quorum"
    report["can_publish"] = passed
    report["found_value_but_blocked"] = bool(not passed)
    return PublicationTierDecision(passed=passed, tier=tier, reasons=reasons, report=report)
