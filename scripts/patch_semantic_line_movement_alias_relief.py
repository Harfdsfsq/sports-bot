from __future__ import annotations

"""Semantic line-movement alias relief for controlled B-tier testing.

This patch does NOT disable the movement guard. It only prevents a known false
negative: the current-price/line guard can mark `semantic_line_movement_failed`
when the candidate has enough fresh B-tier evidence but the selected bookmaker or
line-history alias does not map cleanly back to the current snapshot.

Actual price-integrity failures, stale/missing current offer snapshots, low
EV/edge, duplicate, family and market-quality blockers stay intact.
"""

from typing import Any


def _num(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(str(value).replace(",", "."))
    except Exception:
        return default


def _count(value: Any) -> int:
    try:
        if isinstance(value, (list, tuple, set, dict)):
            return len(value)
        if value in (None, ""):
            return 0
        return int(float(str(value).replace(",", ".")))
    except Exception:
        return 0


def _metric_max(metrics: dict[str, Any], *keys: str) -> int:
    return max((_count(metrics.get(key)) for key in keys), default=0)


def _passes_b_testing_floor(metrics: dict[str, Any]) -> bool:
    books = _metric_max(metrics, "books_count", "bookmaker_count", "price_confirmation_count")
    odds_sources = _metric_max(metrics, "odds_sources_count", "line_sources_count", "sources_count")
    ctx = _metric_max(metrics, "context_sources_count", "confirmation_sources_count")
    ev = max(_num(metrics.get("canonical_ev_pct")), _num(metrics.get("ev_pct")))
    edge = max(_num(metrics.get("canonical_edge_pp")), _num(metrics.get("edge_pp")))
    odds = max(_num(metrics.get("odds")), _num(metrics.get("selected_odds")), _num(metrics.get("price_used_for_ev")))
    return books >= 2 and odds_sources >= 1 and ctx >= 1 and ev >= 4.0 and edge >= 2.3 and 1.70 <= odds <= 2.70


def _has_price_integrity_problem(reasons: list[str]) -> bool:
    return any(
        reason.startswith("price_integrity:")
        or reason.startswith("semantic_selected_price_not_current")
        or reason.startswith("semantic_current_offer_snapshot_stale")
        or reason.startswith("semantic_current_exact_market_price_missing")
        for reason in reasons
    )


def _looks_like_alias_false_negative(metrics: dict[str, Any]) -> bool:
    price_diag = metrics.get("semantic_current_price_guard") if isinstance(metrics.get("semantic_current_price_guard"), dict) else {}
    movement = metrics.get("semantic_line_movement_guard") if isinstance(metrics.get("semantic_line_movement_guard"), dict) else {}
    if bool(price_diag.get("selected_book_repaired_with_current_book")):
        return True
    if _count(price_diag.get("matching_offers")) > 0 and _count(movement.get("matching_entries")) == 0:
        return True
    entries = movement.get("entries") if isinstance(movement.get("entries"), list) else []
    if entries:
        statuses = {str((entry or {}).get("status") or "").strip().lower() for entry in entries if isinstance(entry, dict)}
        # If at least one recent semantic entry is inconclusive/pending and there
        # is no explicit price-integrity issue, treat a hard failed label as an
        # alias/lifecycle mismatch rather than a confirmed bad move.
        if statuses & {"", "pending", "awaiting_next_run", "not_confirmed"}:
            return True
    return False


def install(base: Any) -> dict[str, Any]:
    old = getattr(base, "hard_reject_reasons", None)
    if not callable(old) or getattr(base, "_harizon_semantic_line_alias_relief", False):
        return {"status": "already_installed" if getattr(base, "_harizon_semantic_line_alias_relief", False) else "missing_hard_reject"}

    def hard_reject(candidate: dict[str, Any], metrics: dict[str, Any], sent_index: dict[str, Any]) -> list[str]:
        reasons = list(old(candidate, metrics, sent_index) or [])
        normalized = [str(reason) for reason in reasons]
        if "semantic_line_movement_failed" not in normalized:
            return reasons
        if not _passes_b_testing_floor(metrics):
            return reasons
        if _has_price_integrity_problem(normalized):
            return reasons
        if not _looks_like_alias_false_negative(metrics):
            return reasons
        filtered = [reason for reason in reasons if str(reason) != "semantic_line_movement_failed"]
        filtered.append("semantic_line_movement_alias_repaired_for_b_tier")
        metrics["semantic_line_movement_alias_relief"] = {
            "applied": True,
            "reason": "b_tier_current_price_matched_but_line_history_alias_failed",
            "publication_contract_relaxed": False,
        }
        return list(dict.fromkeys(filtered))

    base.hard_reject_reasons = hard_reject
    base._harizon_semantic_line_alias_relief = True
    return {"status": "installed", "publication_contract_relaxed": False}


__all__ = ["install"]
