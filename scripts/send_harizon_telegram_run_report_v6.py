from __future__ import annotations

"""HARIZON Telegram run report v6.

Thin wrapper over v5 that fixes two normalization issues observed in Telegram:
- `day inventory: 0` when the real value exists under refresh/priority fields;
- `ready model: 0` when ready data exists under alternate debug/summary fields.
"""

import importlib.util
from pathlib import Path
from typing import Any

V5_PATH = Path(__file__).with_name("send_harizon_telegram_run_report_v5.py")


def _load_v5() -> Any:
    spec = importlib.util.spec_from_file_location("harizon_telegram_report_v5", V5_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load v5 report: {V5_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


v5 = _load_v5()
_original_build_truth_payload = v5.build_truth_payload
_original_render_report = v5.render_report


def _first_positive(*values: Any) -> int:
    for value in values:
        number = v5.as_int(value)
        if number > 0:
            return number
    return 0


def _nested_get(row: dict[str, Any], *keys: str) -> Any:
    if not isinstance(row, dict):
        return None
    for key in keys:
        if row.get(key) not in (None, "", [], {}):
            return row.get(key)
    for container_key in ("summary", "coverage", "stats", "totals", "run", "inventory"):
        child = row.get(container_key)
        if isinstance(child, dict):
            found = _nested_get(child, *keys)
            if found not in (None, "", [], {}):
                return found
    return None


def build_truth_payload_v6() -> dict[str, Any]:
    payload = _original_build_truth_payload()
    artifacts = v5.load_artifacts()
    debug = v5.first_dict(artifacts.get("debug"))
    summary = v5.first_dict(debug.get("summary"))
    refresh_plan = v5.first_dict(artifacts.get("refresh_plan"))
    priority_state = v5.first_dict(artifacts.get("priority_state"))
    rescue = v5.first_dict(artifacts.get("rescue"))
    rescue_counts = v5.first_dict(rescue.get("counts"))
    fallback = v5.first_dict(artifacts.get("fallback"))
    pool_counts = v5.first_dict(fallback.get("pool_counts"))

    coverage = dict(payload.get("coverage") or {})
    funnel = dict(payload.get("funnel") or {})

    day_inventory_total = _first_positive(
        coverage.get("day_inventory_total"),
        summary.get("day_inventory_total"),
        summary.get("day_inventory_matches"),
        summary.get("inventory_total"),
        summary.get("matches_total"),
        refresh_plan.get("active_matches"),
        refresh_plan.get("day_inventory_total"),
        refresh_plan.get("matches_total"),
        priority_state.get("active_matches"),
        priority_state.get("day_inventory_total"),
        _nested_get(priority_state, "active_matches", "day_inventory_total", "matches_total"),
    )
    if day_inventory_total <= 0:
        day_inventory_total = max(v5.as_int(coverage.get("matches_seen")), v5.as_int(coverage.get("matches_with_offers")))

    ready_for_model = _first_positive(
        coverage.get("ready_for_model"),
        summary.get("ready_for_model"),
        summary.get("matches_ready_for_model"),
        summary.get("ready_for_model_count"),
        summary.get("model_matches"),
        summary.get("model_debug_matches"),
        rescue_counts.get("candidates_before_quality"),
        pool_counts.get("debug_candidates_before_quality"),
        funnel.get("candidates_before_quality"),
        funnel.get("raw_candidates"),
    )

    coverage["day_inventory_total"] = day_inventory_total
    coverage["ready_for_model"] = ready_for_model
    payload["coverage"] = coverage
    payload["funnel"] = funnel
    payload["version"] = "harizon-telegram-report-v6-single-source-normalized"
    payload.setdefault("normalization_notes", {})
    payload["normalization_notes"].update({
        "day_inventory_total_source": "first_positive(summary/refresh_plan/priority_state/fallback)",
        "ready_for_model_source": "first_positive(summary/rescue/fallback/funnel)",
    })
    return payload


def render_report_v6(payload: dict[str, Any]) -> str:
    text = _original_render_report(payload)
    return text.replace("HARIZON run report v5", "HARIZON run report v6")


v5.build_truth_payload = build_truth_payload_v6
v5.render_report = render_report_v6


if __name__ == "__main__":
    raise SystemExit(v5.main())
