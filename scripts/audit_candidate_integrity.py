from __future__ import annotations

import csv
import json
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.services.candidate_integrity import evaluate_candidate_integrity

UTC = timezone.utc


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _latest_run_archive() -> Path | None:
    roots = [Path(".logs/runs"), Path(".data/history/runs")]
    candidates: list[Path] = []
    for root in roots:
        if root.exists():
            candidates.extend(path for path in root.glob("*/*-run.json") if path.is_file())
    if not candidates:
        return None
    return sorted(candidates, key=lambda p: (p.parent.name, p.name))[-1]


def _load_run_payload() -> tuple[dict[str, Any], str]:
    debug_path = Path(".logs/debug-last-run.json")
    debug = _read_json(debug_path, None)
    if isinstance(debug, dict) and debug:
        return debug, str(debug_path)
    latest = _latest_run_archive()
    if latest is not None:
        payload = _read_json(latest, {})
        if isinstance(payload, dict):
            return payload, str(latest)
    return {}, ""


def _iter_candidate_rows(payload: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    rows: list[tuple[str, dict[str, Any]]] = []
    for bucket in ("candidates", "candidates_before_quality", "shadow_candidates", "candidates_zero_stake", "reused_candidates"):
        for item in payload.get(bucket) or []:
            if isinstance(item, dict):
                rows.append((bucket, item))
    for item in payload.get("forecast_rows") or []:
        if isinstance(item, dict):
            rows.append(("forecast_rows", item))
    return rows


def _compact_row(bucket: str, item: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    source_summary = item.get("source_summary") if isinstance(item.get("source_summary"), dict) else {}
    return {
        "bucket": bucket,
        "match_key": item.get("match_key"),
        "league_name": item.get("league_name"),
        "family": item.get("family"),
        "selection": item.get("selection"),
        "selection_key": item.get("selection_key"),
        "point": item.get("point"),
        "odds": item.get("odds"),
        "selected_price": source_summary.get("selected_price"),
        "bookmaker": item.get("bookmaker") or source_summary.get("selected_bookmaker"),
        "source": source_summary.get("selected_source"),
        "model_mode": item.get("model_mode"),
        "quality_status": source_summary.get("quality_status"),
        "quality_reasons": ";".join(str(x) for x in source_summary.get("quality_reasons", []) if x),
        "issues": ";".join(result.get("issues") or []),
        "suspicious": result.get("suspicious"),
        "blocking": result.get("blocking"),
        "selected_implied_probability": result.get("selected_implied_probability"),
        "recorded_implied_probability": result.get("recorded_implied_probability"),
        "market_probability": result.get("market_probability"),
        "adjusted_probability": result.get("adjusted_probability"),
        "source_summary_adjusted_probability": result.get("source_summary_adjusted_probability"),
        "final_probability": result.get("final_probability"),
        "edge_pct": result.get("edge_pct"),
        "ev_pct": result.get("ev_pct"),
        "canonical_edge_pct": result.get("canonical_edge_pct"),
        "canonical_ev_pct": result.get("canonical_ev_pct"),
    }


def main() -> int:
    out_dir = Path(".data/exports")
    out_dir.mkdir(parents=True, exist_ok=True)
    payload, source_path = _load_run_payload()
    rows = _iter_candidate_rows(payload)

    implied_tol = float(os.getenv("INTEGRITY_ODDS_IMPLIED_MAX_GAP", "0.035") or "0.035")
    adjusted_tol = float(os.getenv("INTEGRITY_ADJUSTED_MAX_GAP", "0.025") or "0.025")
    fair_ratio_limit = float(os.getenv("INTEGRITY_FAIR_ODDS_RATIO_LIMIT", "1.35") or "1.35")
    block_on_issue = str(os.getenv("INTEGRITY_BLOCK_ON_ISSUE", "false")).strip().lower() in {"1", "true", "yes", "on"}

    detail_rows: list[dict[str, Any]] = []
    issue_counts: Counter[str] = Counter()
    by_bucket: Counter[str] = Counter()
    suspicious = 0
    blocking = 0

    for bucket, item in rows:
        result = evaluate_candidate_integrity(
            item,
            implied_tolerance=implied_tol,
            adjusted_tolerance=adjusted_tol,
            fair_odds_ratio_limit=fair_ratio_limit,
            block_on_issue=block_on_issue,
        ).as_dict()
        by_bucket[bucket] += 1
        if result.get("suspicious"):
            suspicious += 1
        if result.get("blocking"):
            blocking += 1
        for issue in result.get("issues") or []:
            issue_counts[issue.split(":", 1)[0]] += 1
        detail_rows.append(_compact_row(bucket, item, result))

    summary = {
        "created_at": datetime.now(UTC).isoformat(),
        "source_path": source_path,
        "count": len(rows),
        "suspicious_candidates": suspicious,
        "blocking_candidates": blocking,
        "by_bucket": dict(by_bucket),
        "issue_counts": dict(issue_counts.most_common()),
        "thresholds": {
            "implied_tolerance": implied_tol,
            "adjusted_tolerance": adjusted_tol,
            "fair_odds_ratio_limit": fair_ratio_limit,
            "block_on_issue": block_on_issue,
        },
        "top_suspicious": [row for row in detail_rows if row.get("suspicious")][:25],
    }

    report = {"summary": summary, "rows": detail_rows}
    (out_dir / "latest-candidate-integrity.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    csv_path = out_dir / "latest-candidate-integrity.csv"
    if detail_rows:
        with csv_path.open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(detail_rows[0].keys()))
            writer.writeheader()
            writer.writerows(detail_rows)
    else:
        csv_path.write_text("bucket,match_key,family,selection,issues,suspicious\n", encoding="utf-8")

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
