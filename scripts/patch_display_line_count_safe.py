from __future__ import annotations

from typing import Any


_MODEL_QUALITY_SOURCES = {"", "model", "raw", "raw_model", "model_quality", "quality_model"}


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


def _view(metrics: dict[str, Any]) -> dict[str, Any]:
    view = dict(metrics)
    raw = _num(view.get("books_count"), 0)
    shown = _display_count(view)
    if raw > 20 and shown > 0:
        view["raw_books_count_before_display_clamp"] = raw
        view["display_books_count"] = shown
        view["books_count"] = shown
    source = str(view.get("quality_score_source") or "").strip().lower()
    if source and source not in _MODEL_QUALITY_SOURCES:
        view["raw_quality_score_source_before_display_label"] = source
        view["quality_score_source"] = "proxy"
    return view


def install(base: Any) -> None:
    if getattr(base, "_display_line_count_safe_installed", False):
        return

    original_pick_block = getattr(base, "pick_block", None)
    if callable(original_pick_block):
        def wrapped_pick_block(index: int, candidate: dict[str, Any], metrics: dict[str, Any], tier: str, stake: float) -> str:
            return original_pick_block(index, candidate, _view(metrics), tier, stake)
        base.pick_block = wrapped_pick_block

    original_build_message = getattr(base, "build_message", None)
    if callable(original_build_message):
        def wrapped_build_message(candidate: dict[str, Any], metrics: dict[str, Any], tier: str, bankroll: dict[str, Any]) -> str:
            return original_build_message(candidate, _view(metrics), tier, bankroll)
        base.build_message = wrapped_build_message

    base._display_line_count_safe_installed = True
