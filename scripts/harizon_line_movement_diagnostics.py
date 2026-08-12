from __future__ import annotations

"""Line movement diagnostics for HARIZON production runs.

This script is diagnostic/watchlist only. It does not publish picks and does not
relax hard guards. It classifies movement failures so we can distinguish true bad
movement from alias/snapshot/freshness lifecycle issues.
"""

import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

EXPORT = Path(".data/exports")
OUT = EXPORT / "latest-line-movement-diagnostics.json"
WATCHLIST = EXPORT / "latest-line-movement-watchlist.json"


def _load(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return {} if default is None else default


def _num(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(str(value).replace(",", "."))
    except Exception:
        return default


def _metrics(row: dict[str, Any]) -> dict[str, Any]:
    return row.get("metrics") if isinstance(row.get("metrics"), dict) else {}


def _candidate_key(row: dict[str, Any]) -> str:
    return "|".join(str(row.get(k) or "") for k in ("match_key", "home_team", "away_team", "selection", "point"))


def _reserve_quality(row: dict[str, Any]) -> float:
    m = _metrics(row)
    q = _num(m.get("reserve_quality_score") or m.get("quality_score") or row.get("reserve_quality_score"), -1.0)
    if q > 0:
        return q
    ev = max(_num(m.get("canonical_ev_pct")), _num(m.get("ev_pct")), 0.0)
    edge = max(_num(m.get("canonical_edge_pp")), _num(m.get("edge_pp")), 0.0)
    odds = _num(m.get("odds"), 0.0)
    score = 38 + min(18, ev * 1.45) + min(16, edge * 3.0)
    if 1.75 <= odds <= 2.55:
        score += 4
    elif odds < 1.70 or odds > 2.90:
        score -= 8
    return round(max(0.0, min(100.0, score)), 1)


def _classify(row: dict[str, Any]) -> list[str]:
    reasons = [str(r) for r in row.get("reject_reasons") or []]
    m = _metrics(row)
    reasons += [str(r) for r in m.get("quality_reasons") or []]
    text = " ".join(reasons).lower()
    classes: list[str] = []
    if "selected_price_not_current" in text or "semantic selected price not current" in text or "semantic_selected_price_not_current" in text:
        classes.append("selected_price_not_current")
    if "current_exact_market_price_missing" in text or "current_offer_snapshot_stale" in text:
        classes.append("current_price_missing_or_stale")
    if "unconfirmed final" in text or "unconfirmed_final" in text:
        classes.append("unconfirmed_final")
    if "not confirmed" in text or "not_confirmed" in text:
        classes.append("not_confirmed")
    if "movement failed" in text or "movement_failed" in text or "line movement failed" in text:
        sem = m.get("semantic_line_movement_guard") if isinstance(m.get("semantic_line_movement_guard"), dict) else {}
        entries = sem.get("entries") if isinstance(sem.get("entries"), list) else []
        if not entries:
            classes.append("line_snapshot_alias_or_missing")
        else:
            statuses = {str((e or {}).get("status") or "").lower() for e in entries if isinstance(e, dict)}
            if statuses & {"movement_failed", "failed"}:
                classes.append("actual_bad_movement")
            elif statuses & {"", "pending", "awaiting_next_run", "not_confirmed"}:
                classes.append("line_snapshot_pending_or_alias")
            else:
                classes.append("line_movement_failed_unknown")
    if "needs_next_cron" in text or "awaiting_next_run" in text:
        classes.append("missing_second_snapshot")
    if "odds_below_global_min" in text or "odds below global min" in text:
        classes.append("odds_below_min")
    if "duplicate" in text:
        classes.append("duplicate")
    if "xg_direction_conflict" in text or "направление" in text:
        classes.append("xg_direction_conflict")
    return list(dict.fromkeys(classes or ["other"]))


def _strong_watch_candidate(row: dict[str, Any], classes: list[str]) -> bool:
    m = _metrics(row)
    q = _reserve_quality(row)
    ev = max(_num(m.get("canonical_ev_pct")), _num(m.get("ev_pct")), 0.0)
    edge = max(_num(m.get("canonical_edge_pp")), _num(m.get("edge_pp")), 0.0)
    odds = _num(m.get("odds"), 0.0)
    technical = bool(set(classes) & {"unconfirmed_final", "not_confirmed", "missing_second_snapshot", "line_snapshot_alias_or_missing", "line_snapshot_pending_or_alias"})
    hard_bad = bool(set(classes) & {"selected_price_not_current", "current_price_missing_or_stale", "odds_below_min", "duplicate", "xg_direction_conflict", "actual_bad_movement"})
    return technical and not hard_bad and q >= 65 and ev >= 4.0 and edge >= 2.3 and 1.70 <= odds <= 2.85


def main() -> int:
    fallback = _load(EXPORT / "latest-controlled-fallback-report.json", {})
    evaluated = fallback.get("evaluated") if isinstance(fallback, dict) and isinstance(fallback.get("evaluated"), list) else []
    rows = [r for r in evaluated if isinstance(r, dict)]
    counts: Counter[str] = Counter()
    samples: list[dict[str, Any]] = []
    watch: list[dict[str, Any]] = []
    for row in rows:
        classes = _classify(row)
        for cls in classes:
            counts[cls] += 1
        item = {
            "key": _candidate_key(row),
            "home_team": row.get("home_team"),
            "away_team": row.get("away_team"),
            "selection": row.get("selection"),
            "point": row.get("point"),
            "odds": _metrics(row).get("odds"),
            "ev_pct": _metrics(row).get("canonical_ev_pct"),
            "edge_pp": _metrics(row).get("canonical_edge_pp"),
            "reserve_quality_score": _reserve_quality(row),
            "classes": classes,
            "reject_reasons": row.get("reject_reasons") or [],
        }
        if len(samples) < 20:
            samples.append(item)
        if _strong_watch_candidate(row, classes):
            watch.append(item)
    payload = {
        "status": "ok",
        "created_at_utc": datetime.now(UTC).isoformat(),
        "evaluated": len(rows),
        "class_counts": dict(counts),
        "samples": samples,
        "watchlist_count": len(watch),
        "publication_contract_relaxed": False,
        "note": "diagnostic/watchlist only; hard guards are unchanged",
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    WATCHLIST.write_text(json.dumps({"created_at_utc": payload["created_at_utc"], "items": watch}, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
