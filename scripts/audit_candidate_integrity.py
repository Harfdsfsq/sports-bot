from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any


def _to_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except Exception:
        return None


def _pct_points(a: float, b: float) -> float:
    return abs(a - b) * 100.0


def audit_candidate(candidate: dict[str, Any], *, implied_tolerance_pp: float = 2.0, adjusted_tolerance_pp: float = 2.0) -> dict[str, Any]:
    reasons: list[str] = []

    odds = _to_float(candidate.get("odds"))
    implied = _to_float(candidate.get("implied_probability"))
    market_prob = _to_float(candidate.get("market_probability"))
    fair_odds = _to_float(candidate.get("fair_odds"))
    adjusted = _to_float(candidate.get("adjusted_probability"))
    final_prob = _to_float(candidate.get("final_probability"))
    edge_pct = _to_float(candidate.get("edge_pct"))
    ev_pct = _to_float(candidate.get("ev_pct"))

    source_summary = candidate.get("source_summary") or {}
    source_adjusted = _to_float(source_summary.get("adjusted_probability"))
    selected_price = _to_float(source_summary.get("selected_price"))

    if odds and implied:
        expected_implied = 1.0 / odds
        mismatch = _pct_points(expected_implied, implied)
        if mismatch > implied_tolerance_pp:
            reasons.append(f"implied_mismatch:{mismatch:.2f}pp")

    if odds and selected_price and abs(odds - selected_price) > 1e-6:
        reasons.append(f"selected_price_mismatch:{abs(odds - selected_price):.4f}")

    if adjusted is not None and source_adjusted is not None:
        mismatch = _pct_points(adjusted, source_adjusted)
        if mismatch > adjusted_tolerance_pp:
            reasons.append(f"adjusted_mismatch:{mismatch:.2f}pp")

    if adjusted is not None and final_prob is not None:
        mismatch = _pct_points(adjusted, final_prob)
        if mismatch > adjusted_tolerance_pp:
            reasons.append(f"final_probability_mismatch:{mismatch:.2f}pp")

    if market_prob and fair_odds:
        expected_fair = 1.0 / market_prob
        if abs(expected_fair - fair_odds) > 0.05:
            reasons.append(f"fair_odds_mismatch:{abs(expected_fair - fair_odds):.4f}")

    if edge_pct is not None and ev_pct is not None and edge_pct < 0 and ev_pct > 0:
        reasons.append("negative_edge_positive_ev_conflict")

    return {
        "match_key": candidate.get("match_key"),
        "selection_key": candidate.get("selection_key"),
        "family": candidate.get("family"),
        "odds": odds,
        "reasons": reasons,
        "suspicious": bool(reasons),
    }


def main() -> int:
    latest_picks = Path(".data/exports/latest-picks.json")
    output = Path(".data/exports/latest-candidate-integrity.json")
    rows = []
    if latest_picks.exists():
        try:
            payload = json.loads(latest_picks.read_text(encoding="utf-8"))
            if isinstance(payload, list):
                rows = [audit_candidate(item) for item in payload if isinstance(item, dict)]
        except Exception as exc:
            rows = [{"suspicious": True, "reasons": [f"read_error:{type(exc).__name__}:{exc}"]}]
    report = {
        "count": len(rows),
        "suspicious_candidates": sum(1 for item in rows if item.get("suspicious")),
        "rows": rows,
    }
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
