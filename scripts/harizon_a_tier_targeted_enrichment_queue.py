from __future__ import annotations

"""Build targeted enrichment queues for A-tier promotion.

No publication side effects. Produces priority lists for provider steps/reports:
- Bzzoiro odds confirmation for rows with odds-api + 2 books + context/value.
- SStats/context projection targets for rows missing the second context source.
- Fast recheck queue for high-value B-tier candidates whose price/movement needs a quick refresh.
"""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

EXPORT = Path(".data/exports")
OUT = EXPORT / "latest-a-tier-targeted-enrichment-queue.json"
FAST_RECHECK = EXPORT / "latest-high-value-recheck-queue.json"


def _load(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return {} if default is None else default


def _rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [r for r in payload if isinstance(r, dict)]
    if isinstance(payload, dict):
        for key in ("rows", "matches", "items", "inventory", "top"):
            value = payload.get(key)
            if isinstance(value, list):
                return [r for r in value if isinstance(r, dict)]
    return []


def _num(v: Any, default: float = 0.0) -> float:
    try:
        if v in (None, ""):
            return default
        return float(str(v).replace(",", "."))
    except Exception:
        return default


def _int(v: Any) -> int:
    try:
        if isinstance(v, (list, tuple, set, dict)):
            return len(v)
        return int(float(v))
    except Exception:
        return 0


def _count(row: dict[str, Any], *keys: str) -> int:
    values: list[int] = []
    for key in keys:
        values.append(_int(row.get(key)))
        for box_key in ("coverage", "metrics", "source_summary", "day_inventory_coverage"):
            box = row.get(box_key) if isinstance(row.get(box_key), dict) else {}
            values.append(_int(box.get(key)))
    return max(values or [0])


def _value_score(row: dict[str, Any]) -> tuple[float, float, float]:
    metrics = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
    ev = max(_num(row.get("ev_pct")), _num(metrics.get("ev_pct")), _num(metrics.get("canonical_ev_pct")))
    edge = max(_num(row.get("edge_pp")), _num(metrics.get("edge_pp")), _num(metrics.get("canonical_edge_pp")))
    odds = max(_num(row.get("odds")), _num(metrics.get("odds")), _num(row.get("selected_odds")))
    return ev, edge, odds


def _base_item(row: dict[str, Any]) -> dict[str, Any]:
    ev, edge, odds = _value_score(row)
    return {
        "match_key": row.get("match_key") or row.get("canonical_match_id"),
        "home_team": row.get("home_team") or row.get("home"),
        "away_team": row.get("away_team") or row.get("away"),
        "kickoff": row.get("commence_time") or row.get("kickoff_utc") or row.get("start_time"),
        "league": row.get("league") or row.get("league_name"),
        "ev_pct": ev,
        "edge_pp": edge,
        "odds": odds,
        "odds_sources": _count(row, "odds_sources_count", "line_sources_count"),
        "books": _count(row, "books_count", "bookmaker_count", "price_confirmation_count"),
        "context_sources": _count(row, "context_sources_count", "confirmation_sources_count"),
    }


def _priority(item: dict[str, Any]) -> float:
    return item["ev_pct"] * 2.0 + item["edge_pp"] * 4.0 + item["books"] * 3.0 + item["context_sources"] * 2.0


def main() -> int:
    truth = _load(EXPORT / "latest-day-inventory-coverage-truth.json", {})
    inv = _load(Path(".data/day_inventory/latest.json"), [])
    plan = _load(EXPORT / "latest-a-tier-coverage-plan.json", {})
    fallback = _load(EXPORT / "latest-controlled-fallback-report.json", {})
    source_rows = _rows(truth.get("rows") if isinstance(truth, dict) else []) or _rows(inv)
    plan_rows = _rows(plan)
    evaluated = _rows(fallback.get("evaluated") if isinstance(fallback, dict) else [])

    bzz_targets: list[dict[str, Any]] = []
    ctx_targets: list[dict[str, Any]] = []
    for row in source_rows + plan_rows:
        item = _base_item(row)
        if item["books"] < 2:
            continue
        missing_odds = item["odds_sources"] < 2
        missing_ctx = item["context_sources"] < 2
        if not (missing_odds or missing_ctx):
            continue
        item["priority"] = round(_priority(item), 2)
        if missing_odds:
            item["recommended_provider"] = "bzzoiro"
            item["recommended_action"] = "confirm_independent_odds_source"
            bzz_targets.append(dict(item))
        if missing_ctx:
            ctx = dict(item)
            ctx["recommended_provider"] = "sstats,bzzoiro"
            ctx["recommended_action"] = "project_second_context_source"
            ctx_targets.append(ctx)

    fast: list[dict[str, Any]] = []
    for row in evaluated:
        item = _base_item(row)
        reasons = " ".join(str(r) for r in row.get("reject_reasons") or [])
        if item["ev_pct"] >= 4.0 and item["edge_pp"] >= 2.3 and 1.70 <= item["odds"] <= 2.85:
            if any(token in reasons for token in ("semantic line movement not confirmed", "semantic_line_movement_not_confirmed", "semantic_line_movement_unconfirmed_final", "current_price_recheck_value_lost")):
                item["priority"] = round(_priority(item) + 20, 2)
                item["recheck_reason"] = reasons[:300]
                fast.append(item)

    bzz_targets = sorted(bzz_targets, key=lambda r: -_num(r.get("priority")))[:80]
    ctx_targets = sorted(ctx_targets, key=lambda r: -_num(r.get("priority")))[:80]
    fast = sorted(fast, key=lambda r: -_num(r.get("priority")))[:30]
    payload = {
        "status": "ok",
        "created_at_utc": datetime.now(UTC).isoformat(),
        "bzzoiro_odds_targets": bzz_targets,
        "context_projection_targets": ctx_targets,
        "high_value_recheck_targets": fast,
        "summary": {
            "bzzoiro_odds_target_count": len(bzz_targets),
            "context_projection_target_count": len(ctx_targets),
            "high_value_recheck_target_count": len(fast),
        },
        "publication_contract_relaxed": False,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    FAST_RECHECK.write_text(json.dumps({"created_at_utc": payload["created_at_utc"], "items": fast}, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(payload["summary"], ensure_ascii=False))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
