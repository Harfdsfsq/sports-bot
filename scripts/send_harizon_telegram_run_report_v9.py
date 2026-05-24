from __future__ import annotations

"""HARIZON Telegram run report v9.

Small compatibility wrapper around v8.

Why it exists:
- v8 correctly renders progressive/coverage truth, but it treats every
  controlled-fallback pool counter as a pre-evaluation filter.
- Source-pool counters such as ``debug_candidates_before_quality: 4`` are not
  filters; those candidates were actually evaluated by fallback.
- This wrapper patches only the v8 pool-filter classifier before v8 builds and
  sends the report. It does not touch candidate selection or publication rules.
"""

from importlib import import_module
from typing import Any

SOURCE_POOL_KEYS = {
    "debug_candidates_before_quality",
    "latest_rescue_candidates",
    "artifact_rescue_candidates",
    "candidates_before_quality",
    "passed_candidates",
    "publishable_candidates",
    "day_inventory_membership_keys",
}

TRUE_FILTER_SUFFIXES = (
    "_not_in_day_inventory",
    "_stale_or_outside_window",
    "_stale_payload",
    "_canonical_negative_value_prefilter",
    "_negative_value_prefilter",
    "_line_guard_blocked",
    "_outside_publish_window",
)

TRUE_FILTER_SUBSTRINGS = (
    "_prefilter",
    "not_in_day_inventory",
    "stale_or_outside_window",
    "outside_window",
    "stale_payload",
    "line_guard",
)


def _as_int(value: Any) -> int:
    try:
        if value in (None, ""):
            return 0
        return int(float(str(value)))
    except Exception:
        return 0


def _is_true_pre_evaluation_filter_key(key: str) -> bool:
    name = str(key or "").strip()
    if not name:
        return False
    if name in SOURCE_POOL_KEYS:
        return False
    if name.endswith("_duplicate_in_pool"):
        return False
    if name.endswith(TRUE_FILTER_SUFFIXES):
        return True
    return any(token in name for token in TRUE_FILTER_SUBSTRINGS)


def filtered_pool_filter_counts(pool_counts: dict[str, Any]) -> dict[str, int]:
    """Return only real pre-evaluation filter counters.

    Pool-source counters must not be converted into no-publish reasons.  Example:
    ``debug_candidates_before_quality: 4`` means four rows came from that source,
    not that four rows were rejected before evaluation.
    """
    if not isinstance(pool_counts, dict):
        return {}
    out: dict[str, int] = {}
    for key, value in pool_counts.items():
        if not _is_true_pre_evaluation_filter_key(str(key)):
            continue
        count = _as_int(value)
        if count > 0:
            out[str(key)] = count
    return out


def patch_v8_module(v8: Any) -> None:
    v8._fallback_pool_filter_counts = filtered_pool_filter_counts


def main() -> int:
    v8 = import_module("scripts.send_harizon_telegram_run_report_v8")
    patch_v8_module(v8)
    return int(v8.main() or 0)


if __name__ == "__main__":
    raise SystemExit(main())
