from __future__ import annotations

import json
import math
import os
from pathlib import Path
from typing import Any

EXPORT_ROOT = Path(os.getenv("EXPORT_ROOT", ".data/exports"))
MAX_RATIO = float(os.getenv("ODDS_AUDIT_MAX_PRICE_VS_CONSENSUS_RATIO", "1.18"))
MAX_GAP_PP = float(os.getenv("ODDS_AUDIT_MAX_ABS_IMPLIED_GAP_PP", "12.0"))
FAIL_ON_TOTALS = str(os.getenv("ODDS_AUDIT_FAIL_ON_TOTALS", "true")).strip().lower() in {"1", "true", "yes", "on"}

def _load_json(path: Path) -> Any:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None

def _iter_candidate_rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        rows = payload.get("rows") or payload.get("candidates") or payload.get("items") or []
        return [item for item in rows if isinstance(item, dict)]
    return []

def _num(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except Exception:
        return None

def _pick_latest_payload() -> Path | None:
    candidates = [
        EXPORT_ROOT / "latest-bets.json",
        EXPORT_ROOT / "latest-picks.json",
    ]
    for path in candidates:
        if path.exists():
            return path
    dated = sorted(EXPORT_ROOT.glob("20*/**/*bets.json"))
    return dated[-1] if dated else None

def _row_family(row: dict[str, Any]) -> str:
    return str(row.get("family") or row.get("forecast_family") or row.get("market") or "").strip()

def _row_selection(row: dict[str, Any]) -> str:
    return str(row.get("selection") or row.get("forecast_selection") or "").strip()

def _row_point(row: dict[str, Any]) -> Any:
    return row.get("point", row.get("forecast_line"))

def main() -> int:
    payload_path = _pick_latest_payload()
    if payload_path is None:
        print(json.dumps({"ok": False, "error": "no export payload found", "export_root": str(EXPORT_ROOT)}, ensure_ascii=False, indent=2))
        return 1

    payload = _load_json(payload_path)
    rows = _iter_candidate_rows(payload)
    problems: list[dict[str, Any]] = []

    for row in rows:
        family = _row_family(row)
        odds = _num(row.get("odds") or row.get("forecast_odds"))
        market_prob = _num(row.get("market_probability") or row.get("forecast_market_probability"))
        if odds is None or market_prob is None or odds <= 1.0 or market_prob <= 0:
            continue

        if market_prob > 1.0:
            market_prob = market_prob / 100.0

        consensus_fair_odds = 1.0 / max(market_prob, 0.0001)
        ratio = odds / consensus_fair_odds
        implied_prob = 1.0 / max(odds, 0.0001)
        gap_pp = abs((market_prob - implied_prob) * 100.0)

        suspicious = ratio > MAX_RATIO or gap_pp > MAX_GAP_PP
        if FAIL_ON_TOTALS and family == "totals" and suspicious:
            problems.append({
                "family": family,
                "selection": _row_selection(row),
                "point": _row_point(row),
                "odds": round(odds, 4),
                "market_probability": round(market_prob, 4),
                "consensus_fair_odds": round(consensus_fair_odds, 4),
                "ratio_vs_consensus": round(ratio, 4),
                "abs_gap_pp": round(gap_pp, 2),
                "selected_bookmaker": row.get("selected_bookmaker") or row.get("forecast_bookmaker"),
                "selected_source": row.get("selected_source") or row.get("forecast_odds_source"),
                "match_key": row.get("match_key"),
            })
        elif suspicious:
            problems.append({
                "family": family,
                "selection": _row_selection(row),
                "point": _row_point(row),
                "odds": round(odds, 4),
                "market_probability": round(market_prob, 4),
                "consensus_fair_odds": round(consensus_fair_odds, 4),
                "ratio_vs_consensus": round(ratio, 4),
                "abs_gap_pp": round(gap_pp, 2),
                "selected_bookmaker": row.get("selected_bookmaker") or row.get("forecast_bookmaker"),
                "selected_source": row.get("selected_source") or row.get("forecast_odds_source"),
                "match_key": row.get("match_key"),
            })

    result = {
        "ok": len(problems) == 0,
        "payload_path": str(payload_path),
        "checked_rows": len(rows),
        "problems_found": len(problems),
        "max_ratio_allowed": MAX_RATIO,
        "max_abs_gap_pp_allowed": MAX_GAP_PP,
        "problems": problems[:50],
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if len(problems) == 0 else 2

if __name__ == "__main__":
    raise SystemExit(main())
