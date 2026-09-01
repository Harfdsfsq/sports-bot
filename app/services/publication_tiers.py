from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from app.services.coverage_contract import sync_candidate_publish_coverage
from app.services.publication_thresholds import (
    publish_min_books,
    publish_min_context_sources,
    publish_min_odds_sources,
)
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
    """Classify a candidate by the HARIZON publication contract.

    A/B tiers remain useful for labels and ranking, but both tiers must satisfy
    the same hard evidence floor before Telegram: 2 independent odds sources,
    2 bookmaker/price confirmations, 2 context sources, and a line-movement
    decision that is either confirmed or final because no regular cron remains.
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

    price_or_bookmaker_count = max(books_count, price_count)

    hard_odds = publish_min_odds_sources(settings)
    hard_books = publish_min_books(settings)
    hard_context = publish_min_context_sources(settings)
    tier_a_books = max(hard_books, _env_int("PUBLISH_TIER_A_MIN_BOOKS", hard_books, hard_books))
    tier_a_context = max(hard_context, _env_int("PUBLISH_TIER_A_MIN_CONTEXT_SOURCES", hard_context, hard_context))
    tier_a_odds = max(hard_odds, _env_int("PUBLISH_TIER_A_MIN_ODDS_SOURCES", hard_odds, hard_odds))
    tier_b_books = max(hard_books, _env_int("PUBLISH_TIER_B_MIN_BOOKS", hard_books, hard_books))
    tier_b_context = max(hard_context, _env_int("PUBLISH_TIER_B_MIN_CONTEXT_SOURCES", hard_context, hard_context))
    tier_b_odds = max(hard_odds, _env_int("PUBLISH_TIER_B_MIN_ODDS_SOURCES", hard_odds, hard_odds))

    movement = evaluate_and_record_line_movement(candidate, settings, now=now)
    report["line_movement"] = movement
    report["tier_thresholds"] = {
        "A": {
            "min_independent_odds_sources": tier_a_odds,
            "min_bookmakers_or_price_confirmations": tier_a_books,
            "min_context_sources": tier_a_context,
        },
        "B": {
            "min_independent_odds_sources": tier_b_odds,
            "min_bookmakers_or_price_confirmations": tier_b_books,
            "min_context_sources": tier_b_context,
            "requires_movement_confirmed": True,
        },
    }
    report["price_sources_count"] = price_count
    report["bookmakers_or_price_confirmations_count"] = price_or_bookmaker_count
    report["found_value"] = True

    is_a = odds_count >= tier_a_odds and price_or_bookmaker_count >= tier_a_books and context_count >= tier_a_context
    is_b = odds_count >= tier_b_odds and price_or_bookmaker_count >= tier_b_books and context_count >= tier_b_context
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
                f"odds_sources={odds_count}/{tier_b_odds};"
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
    report["tier_a_bookmaker_quorum_passed"] = is_a
    report["tier_b_bookmaker_quorum_passed"] = is_b
    report["tier_a_strict_coverage_passed"] = is_a
    report["tier_b_strict_coverage_passed"] = is_b
    report["tier_confirmation_mode"] = "strict_independent_sources"
    report["tier_b_confirmation_mode"] = "strict_independent_sources" if is_b else "none"
    report["can_publish"] = passed
    report["found_value_but_blocked"] = bool(not passed)
    return PublicationTierDecision(passed=passed, tier=tier, reasons=reasons, report=report)
