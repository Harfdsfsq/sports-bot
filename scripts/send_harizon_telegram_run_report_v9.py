from __future__ import annotations

"""HARIZON Telegram run report v9.

Compatibility wrapper around v8 with two reporting fixes:

1. Pool source counters are not pre-evaluation filters.  For example,
   ``debug_candidates_before_quality: 4`` means four rows were loaded from that
   source, not four rejected candidates.
2. When all raw candidates are prefiltered by canonical/controlled value before
   fallback, the conclusion should say that the value gate worked, not that the
   mapping/candidate factory is broken.

This module does not change candidate selection or publication rules.
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

NEGATIVE_VALUE_TOKENS = (
    "canonical_negative_value_prefilter",
    "negative_value_prefilter",
    "отрицательная",
    "negative",
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
    """Return only real pre-evaluation filter counters."""
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


def _pool_filters(payload: dict[str, Any]) -> dict[str, int]:
    diag = payload.get("diagnostics") if isinstance(payload.get("diagnostics"), dict) else {}
    filters = diag.get("controlled_fallback_pool_filter_counts") if isinstance(diag.get("controlled_fallback_pool_filter_counts"), dict) else {}
    return {str(k): _as_int(v) for k, v in filters.items() if _as_int(v) > 0}


def _has_negative_value_pool_filter(payload: dict[str, Any]) -> bool:
    filters = _pool_filters(payload)
    if not filters:
        return False
    joined = " ".join(filters.keys()).lower()
    return any(token in joined for token in NEGATIVE_VALUE_TOKENS)


def _patch_report_conclusion(text: str, payload: dict[str, Any]) -> str:
    if not _has_negative_value_pool_filter(payload):
        return text
    old_variants = [
        "• Нужно смотреть candidate factory/mapping: линии и контекст есть, но кандидаты не дошли до проверки.",
        "• Главный технический bottleneck: мало матчей с 2 independent odds sources. Нужно добирать SportLogic/Bzzoiro overlap, а не ослаблять guards.",
    ]
    new = (
        "• Candidate pipeline работает: raw-кандидат был найден, но отфильтрован до fallback "
        "после контрольного пересчёта value. Это корректный safety gate: отрицательный "
        "post-calibration EV/edge не должен попадать в резервную публикацию."
    )
    for old in old_variants:
        if old in text:
            return text.replace(old, new)
    marker = "📌 Вывод"
    idx = text.find(marker)
    if idx >= 0:
        next_line = text.find("\n", idx)
        if next_line >= 0:
            return text[: next_line + 1] + new + "\n" + text[next_line + 1 :]
    return text


def patch_v8_module(v8: Any) -> None:
    v8._fallback_pool_filter_counts = filtered_pool_filter_counts
    original_render = v8.render

    def render_v9(payload: dict[str, Any]) -> str:
        return _patch_report_conclusion(original_render(payload), payload)

    v8.render = render_v9
    try:
        v8.v7.render = render_v9
        v8.v7.v5.render = render_v9
    except Exception:
        pass


def main() -> int:
    v8 = import_module("scripts.send_harizon_telegram_run_report_v8")
    patch_v8_module(v8)
    return int(v8.main() or 0)


if __name__ == "__main__":
    raise SystemExit(main())
