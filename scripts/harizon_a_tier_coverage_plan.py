from __future__ import annotations

"""A-tier coverage planner.

Builds a targeted list of matches where one extra independent odds/context source
would move the row toward A-tier. Diagnostic/plan only; no publication changes.
"""

import json
from pathlib import Path
from typing import Any

EXPORT = Path(".data/exports")
OUT = EXPORT / "latest-a-tier-coverage-plan.json"


def _load(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return {} if default is None else default


def _int(v: Any) -> int:
    try:
        if isinstance(v, (list, tuple, set, dict)):
            return len(v)
        return int(float(v))
    except Exception:
        return 0


def _rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [r for r in payload if isinstance(r, dict)]
    if isinstance(payload, dict):
        for key in ("rows", "matches", "items", "inventory"):
            if isinstance(payload.get(key), list):
                return [r for r in payload[key] if isinstance(r, dict)]
    return []


def _count(row: dict[str, Any], *keys: str) -> int:
    vals = []
    for key in keys:
        vals.append(_int(row.get(key)))
        cov = row.get("coverage") if isinstance(row.get("coverage"), dict) else {}
        vals.append(_int(cov.get(key)))
    return max(vals or [0])


def main() -> int:
    truth = _load(EXPORT / "latest-day-inventory-coverage-truth.json", {})
    inventory = _load(Path(".data/day_inventory/latest.json"), [])
    rows = _rows(truth.get("rows") if isinstance(truth, dict) else []) or _rows(inventory)
    plan: list[dict[str, Any]] = []
    for row in rows:
        odds = _count(row, "odds_sources_count", "line_sources_count")
        books = _count(row, "price_confirmation_count", "books_count", "bookmaker_count")
        ctx = _count(row, "context_sources_count", "confirmation_sources_count")
        if books < 2:
            continue
        missing = []
        if odds < 2:
            missing.append("independent_odds_source")
        if ctx < 2:
            missing.append("second_context_source")
        if not missing:
            continue
        priority = 0
        if odds >= 1 and ctx >= 1: priority += 5
        if books >= 2: priority += 3
        plan.append({"match_key": row.get("match_key") or row.get("canonical_match_id"), "home_team": row.get("home_team") or row.get("home"), "away_team": row.get("away_team") or row.get("away"), "kickoff": row.get("commence_time") or row.get("kickoff_utc"), "odds_sources": odds, "books": books, "context_sources": ctx, "missing_for_a_tier": missing, "recommended_actions": ["bzzoiro_odds_confirm" if "independent_odds_source" in missing else "", "sstats_or_bzzoiro_context_confirm" if "second_context_source" in missing else ""], "priority": priority})
    plan = sorted(plan, key=lambda r: (-_int(r.get("priority")), str(r.get("kickoff") or "")))[:80]
    payload = {"status": "ok", "candidate_count": len(plan), "top": plan[:25], "summary": {"needs_independent_odds": sum(1 for r in plan if "independent_odds_source" in r.get("missing_for_a_tier", [])), "needs_second_context": sum(1 for r in plan if "second_context_source" in r.get("missing_for_a_tier", []))}}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
