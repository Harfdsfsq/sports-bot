#!/usr/bin/env python3
from __future__ import annotations
import json
from pathlib import Path
from typing import Any

ROOT = Path(".data/exports")
OUT = ROOT / "odds-integrity-report.json"


def _load(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _f(v: Any, default: float = 0.0) -> float:
    try:
        if v in ("", None):
            return default
        return float(v)
    except Exception:
        return default


def main() -> int:
    picks = _load(ROOT / "latest-picks.json", [])
    suspicious = []
    for item in picks if isinstance(picks, list) else []:
        if not isinstance(item, dict):
            continue
        odds = _f(item.get("odds"))
        implied = _f(item.get("implied_probability"))
        adjusted = _f(item.get("adjusted_probability"))
        final = _f(item.get("final_probability"), adjusted)
        ss = dict(item.get("source_summary") or {})
        ss_adjusted = _f(ss.get("adjusted_probability"), adjusted)
        issues = []
        if odds > 1.01:
            calc = 1.0 / odds
            if abs(calc - implied) > 0.02:
                issues.append("implied_mismatch")
        if abs(adjusted - final) > 0.02:
            issues.append("final_probability_mismatch")
        if abs(adjusted - ss_adjusted) > 0.02:
            issues.append("source_summary_adjusted_mismatch")
        if _f(item.get("edge_pct")) < 0 and _f(item.get("ev_pct")) > 0:
            issues.append("edge_ev_conflict")
        if issues:
            suspicious.append({
                "match_key": item.get("match_key"),
                "home_team": item.get("home_team"),
                "away_team": item.get("away_team"),
                "family": item.get("family"),
                "selection": item.get("selection"),
                "odds": odds,
                "issues": issues,
            })
    report = {
        "candidates_seen": len(picks) if isinstance(picks, list) else 0,
        "suspicious_candidates": len(suspicious),
        "items": suspicious,
    }
    ROOT.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
