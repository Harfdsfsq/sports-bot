"""Preserve PredictionRunner._filter_matches return shape under Focused Alpha.

The native runner and near-window priority wrapper expect ``(matches, metadata)``.
The first Focused Alpha scope wrapper converted that tuple to a list, which caused the
outer wrapper to fail before any provider enrichment. This patch reuses the original
pre-scope filter and applies the focused cohort without changing its public contract.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

_INSTALLED = False


def split_filter_result(result: Any) -> tuple[list[Any], Any, bool]:
    """Normalize a filter result while remembering whether it was a pair."""
    if isinstance(result, tuple) and len(result) == 2:
        rows, metadata = result
        return list(rows or []), metadata, True
    return list(result or []), None, False


def restore_filter_result(
    rows: list[Any],
    metadata: Any,
    returned_pair: bool,
    *,
    focused_declared: bool,
    original_rows: int,
) -> Any:
    """Return the same shape as the wrapped filter and add non-invasive metadata."""
    if not returned_pair:
        return rows
    if isinstance(metadata, dict):
        metadata = dict(metadata)
        metadata["focused_alpha_model_scope_declared"] = focused_declared
        metadata["focused_alpha_model_targets"] = len(rows)
        metadata["focused_alpha_original_window_targets"] = original_rows
    return rows, metadata


def install(prediction_runner: Any) -> dict[str, Any]:
    global _INSTALLED
    if _INSTALLED:
        return {"status": "already_installed"}

    from app.services import daily_coverage_full_inventory_provider_patch as scope

    inner_filter = scope._ORIGINAL_FILTER
    current_filter = prediction_runner._filter_matches
    if not callable(inner_filter):
        return {
            "status": "skipped_missing_original_filter",
            "current_filter": getattr(current_filter, "__name__", type(current_filter).__name__),
        }
    if getattr(current_filter, "_harizon_focused_alpha_filter_contract_v2", False):
        _INSTALLED = True
        return {"status": "already_patched"}

    def filter_matches_contract_safe(
        self: Any,
        matches: list[Any],
        now_utc: datetime,
    ) -> Any:
        self._harizon_full_horizon_coverage_matches = scope._coverage_horizon_matches(
            self,
            list(matches or []),
            now_utc,
        )
        original_result = inner_filter(self, matches, now_utc)
        publication_window, metadata, returned_pair = split_filter_result(original_result)
        focused, declared = scope._focused_model_scope(self, publication_window)
        selected = focused if declared else publication_window
        self._harizon_focused_alpha_model_scope_declared = declared
        self._harizon_focused_alpha_model_targets = len(selected)
        self._harizon_original_model_window_targets = len(publication_window)
        return restore_filter_result(
            selected,
            metadata,
            returned_pair,
            focused_declared=declared,
            original_rows=len(publication_window),
        )

    filter_matches_contract_safe._harizon_full_inventory_filter_capture = True
    filter_matches_contract_safe._harizon_focused_alpha_filter_contract_v2 = True
    prediction_runner._filter_matches = filter_matches_contract_safe
    _INSTALLED = True
    return {
        "status": "installed",
        "preserves_tuple_contract": True,
        "preserves_list_contract": True,
        "model_scope": "focused_alpha_target_match_keys",
        "publication_contract_relaxed": False,
    }


__all__ = ["install", "restore_filter_result", "split_filter_result"]
