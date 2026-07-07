from __future__ import annotations

from typing import Any


def _num(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ""):
            return default
        return int(float(str(value)))
    except Exception:
        return default


def _display_count(metrics: dict[str, Any]) -> int:
    raw = _num(metrics.get("books_count"), 0)
    if raw <= 20:
        return raw
    quorum = metrics.get("tier_b_bookmaker_quorum") if isinstance(metrics.get("tier_b_bookmaker_quorum"), dict) else {}
    return max(
        _num(metrics.get("priced_books_count"), 0),
        _num(quorum.get("priced_books_count"), 0),
        _num(metrics.get("odds_sources_count"), 0),
        _num(metrics.get("confirmation_sources_count"), 0),
        _num(metrics.get("sources_count"), 0),
        2,
    )


def install(base: Any) -> None:
    original = getattr(base, "pick_block", None)
    if not callable(original) or getattr(base, "_display_line_count_safe_installed", False):
        return

    def wrapped(index: int, candidate: dict[str, Any], metrics: dict[str, Any], tier: str, stake: float) -> str:
        view = dict(metrics)
        raw = _num(view.get("books_count"), 0)
        shown = _display_count(view)
        if raw > 20 and shown > 0:
            view["raw_books_count_before_display_clamp"] = raw
            view["display_books_count"] = shown
            view["books_count"] = shown
        return original(index, candidate, view, tier, stake)

    base.pick_block = wrapped
    base._display_line_count_safe_installed = True
