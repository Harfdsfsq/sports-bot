from __future__ import annotations

"""Append real prediction candidates to the forward-test ledger.

v8 notes:
- api_coverage-only rows are opportunity evidence, not predictions.  They stay in
  candidate-opportunity/calibration audits but are not appended to
  .data/prediction-ledger.jsonl.
- the ledger keeps only rows that reached a prediction stage: before/after
  quality, value patch, quality relief, controlled fallback, or publication.
- sparse value rows without a line are merged into the unique line-bearing row
  from the same match/family/selection.
"""

import json
import os
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

UTC = timezone.utc
ROOT = Path(".").resolve()
EXPORT_DIR = ROOT / ".data" / "exports"
LEDGER = ROOT / ".data" / "prediction-ledger.jsonl"
SUMMARY = EXPORT_DIR / "latest-prediction-ledger-summary.json"

PREDICTION_STAGES = {
    "before_quality",
    "after_quality",
    "value_patch",
    "quality_relief",
    "fallback",
    "normalized_publication",
}
CORE_METRIC_FIELDS = ("home_team", "away_team", "odds", "ev_pct", "edge_pp")


def load_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def as_float(value: Any, default: float | None = None) -> float | None:
    try:
        if value in (None, ""):
            return default
        return float(str(value).replace(",", "."))
    except Exception:
        return default


def as_int(value: Any, default: int | None = None) -> int | None:
    try:
        if value in (None, ""):
            return default
        return int(float(str(value)))
    except Exception:
        return default


def first_nonempty(row: dict[str, Any], *keys: str) -> Any:
    metrics = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
    for key in keys:
        value = row.get(key)
        if value not in (None, "", [], {}):
            return value
        value = metrics.get(key)
        if value not in (None, "", [], {}):
            return value
    return None


def extract_point(row: dict[str, Any]) -> Any:
    value = first_nonempty(row, "point", "line", "total", "handicap")
    if value not in (None, ""):
        return value
    text = " ".join(str(first_nonempty(row, k) or "") for k in ("selection", "selection_key", "market", "market_name", "bet_name"))
    m = re.search(r"(?<!\d)(\d{1,2}(?:[.,_]\d{1,2})?)(?!\d)", text)
    if not m:
        return ""
    raw = m.group(1).replace("_", ".").replace(",", ".")
    try:
        return float(raw)
    except Exception:
        return raw


def point_token(value: Any) -> str:
    if value in (None, ""):
        return ""
    try:
        return f"{float(str(value).replace(',', '.')):.3f}".rstrip("0").rstrip(".")
    except Exception:
        return norm(value)


def flatten_row(row: dict[str, Any]) -> dict[str, Any]:
    row = dict(row or {})
    metrics = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
    out = dict(metrics)
    out.update({k: v for k, v in row.items() if k != "metrics"})
    aliases = {
        "home_team": ("home_team", "home", "home_name"),
        "away_team": ("away_team", "away", "away_name"),
        "kickoff_utc": ("kickoff_utc", "commence_time", "start_time"),
        "family": ("family", "market_family", "market"),
        "selection": ("selection", "selection_key", "side", "pick"),
        "odds": ("odds", "selected_odds", "best_odds"),
        "ev_pct": ("ev_pct", "ev", "canonical_ev_pct", "canonical_ev"),
        "edge_pp": ("edge_pp", "edge_pct", "canonical_edge_pp", "market_edge_pp"),
        "confidence": ("confidence", "confidence_score"),
        "quality": ("quality", "quality_score"),
        "odds_sources_count": ("odds_sources_count", "independent_odds_sources_count", "exact_odds_sources_count", "sources_count"),
        "context_sources_count": ("context_sources_count", "confirmation_sources_count"),
        "books_count": ("books_count", "exact_books_count"),
    }
    for target, keys in aliases.items():
        if out.get(target) not in (None, "", [], {}):
            continue
        value = first_nonempty(row, *keys)
        if value not in (None, "", [], {}):
            out[target] = value
    if out.get("point") in (None, ""):
        out["point"] = extract_point(out)
    return out


def candidate_rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [flatten_row(x) for x in payload if isinstance(x, dict)]
    if not isinstance(payload, dict):
        return []
    out: list[dict[str, Any]] = []
    keys = (
        "candidates", "rows", "data", "evaluated", "blocked_top", "near_miss",
        "selected", "selected_all", "published_candidates", "sample", "input_sample",
        "output_sample", "rows_sample", "candidate_sample", "rejected_samples",
    )
    for key in keys:
        value = payload.get(key)
        if isinstance(value, list):
            out.extend(flatten_row(x) for x in value if isinstance(x, dict))
        elif isinstance(value, dict):
            out.append(flatten_row(value))
    decision = payload.get("decision") if isinstance(payload.get("decision"), dict) else None
    if decision:
        out.extend(candidate_rows(decision))
    return out


def identity(row: dict[str, Any]) -> str:
    row = flatten_row(row)
    base = row.get("match_key") or f"{row.get('home_team')}|{row.get('away_team')}|{row.get('kickoff_utc')}"
    return "|".join([
        norm(base),
        norm(row.get("family")),
        norm(row.get("selection")),
        point_token(extract_point(row)),
    ])


def key_base(key_value: str) -> str:
    parts = str(key_value or "").split("|")
    return "|".join(parts[:3]) if len(parts) >= 4 else str(key_value or "")


def key_point(key_value: str) -> str:
    parts = str(key_value or "").split("|")
    return parts[-1] if len(parts) >= 4 else ""


def row_rank(row: dict[str, Any]) -> tuple[int, int, int, int, int, int]:
    row = flatten_row(row)
    return (
        int(row.get("home_team") not in (None, "")) + int(row.get("away_team") not in (None, "")),
        int(row.get("odds") not in (None, "")),
        int(row.get("ev_pct") not in (None, "")),
        int(row.get("edge_pp") not in (None, "")),
        int(row.get("quality") not in (None, "")),
        int(bool(reasons(row))),
    )


def merge_row(base: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    base = flatten_row(base) if base else {}
    incoming = flatten_row(incoming) if incoming else {}
    if row_rank(incoming) > row_rank(base):
        merged = dict(incoming)
        fill = base
    else:
        merged = dict(base)
        fill = incoming
    for key, value in fill.items():
        if merged.get(key) in (None, "", [], {}) and value not in (None, "", [], {}):
            merged[key] = value
    return flatten_row(merged)


def collapse_missing_line_rows(merged: dict[str, dict[str, Any]], stage_seen: dict[str, set[str]]) -> tuple[int, int]:
    by_base: dict[str, list[str]] = defaultdict(list)
    for key in merged:
        if key_point(key):
            by_base[key_base(key)].append(key)
    collapsed = 0
    ambiguous = 0
    for source in list(merged):
        if key_point(source):
            continue
        targets = by_base.get(key_base(source), [])
        if len(targets) == 1:
            target = targets[0]
            merged[target] = merge_row(merged[target], merged[source])
            stage_seen[target].update(stage_seen.get(source, set()))
            merged.pop(source, None)
            stage_seen.pop(source, None)
            collapsed += 1
        elif len(targets) > 1:
            ambiguous += 1
    return collapsed, ambiguous


def reasons(row: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for name in ("reasons", "reject_reasons", "reject_reasons_ru", "quality_reasons"):
        value = row.get(name)
        if isinstance(value, list):
            out.extend(str(x) for x in value if str(x).strip())
        elif isinstance(value, str) and value.strip():
            out.extend(x.strip() for x in re.split(r"[,|;/]+", value) if x.strip())
    seen = set()
    result = []
    for item in out:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


def existing_ids() -> set[str]:
    ids: set[str] = set()
    if not LEDGER.exists():
        return ids
    for line in LEDGER.read_text(encoding="utf-8").splitlines():
        try:
            obj = json.loads(line)
            if obj.get("ledger_id"):
                ids.add(str(obj["ledger_id"]))
        except Exception:
            continue
    return ids


def collect_current_rows() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    sources = [
        ("before_quality", EXPORT_DIR / "latest-candidates-before-quality.json"),
        ("after_quality", EXPORT_DIR / "latest-candidates-after-quality.json"),
        ("value_patch", EXPORT_DIR / "latest-candidate-value-runtime-patch.json"),
        ("api_coverage", EXPORT_DIR / "latest-api-coverage-consensus-runtime-patch.json"),
        ("quality_relief", EXPORT_DIR / "latest-quality-consensus-safe-relief.json"),
        ("fallback", EXPORT_DIR / "latest-controlled-fallback-report.json"),
        ("normalized_publication", EXPORT_DIR / "latest-normalized-publication-payloads.json"),
    ]
    merged: dict[str, dict[str, Any]] = {}
    stage_seen: dict[str, set[str]] = defaultdict(set)
    raw_rows = 0
    for stage, path in sources:
        for row in candidate_rows(load_json(path, None)):
            raw_rows += 1
            k = identity(row)
            merged[k] = merge_row(merged.get(k, {}), row)
            stage_seen[k].add(stage)
    collapsed, ambiguous = collapse_missing_line_rows(merged, stage_seen)

    run_id = os.getenv("GITHUB_RUN_ID") or datetime.now(UTC).strftime("%Y%m%d%H%M%S")
    rows_out: list[dict[str, Any]] = []
    excluded_coverage_only = 0
    for key, row in merged.items():
        stages = set(stage_seen[key])
        if not (stages & PREDICTION_STAGES):
            excluded_coverage_only += 1
            continue
        row = flatten_row(row)
        r = reasons(row)
        status = "published" if row.get("telegram_sent") is True or row.get("published") is True else "rejected_or_watch"
        if any("watch" in x.lower() or "watch only" in x.lower() for x in r):
            status = "watch_only"
        rows_out.append({
            "ledger_id": f"{run_id}|{key}",
            "run_id": run_id,
            "created_at_utc": datetime.now(UTC).isoformat(),
            "candidate_key": key,
            "status": status,
            "stage_seen": sorted(stages),
            "match_key": row.get("match_key"),
            "home_team": row.get("home_team"),
            "away_team": row.get("away_team"),
            "league_name": row.get("league_name"),
            "kickoff_utc": row.get("kickoff_utc") or row.get("commence_time"),
            "family": row.get("family"),
            "selection": row.get("selection"),
            "point": extract_point(row),
            "odds": as_float(row.get("odds")),
            "ev_pct": as_float(row.get("ev_pct")),
            "edge_pp": as_float(row.get("edge_pp")),
            "confidence": as_float(row.get("confidence")),
            "quality": as_float(row.get("quality")),
            "odds_sources_count": as_int(row.get("odds_sources_count")),
            "context_sources_count": as_int(row.get("context_sources_count")),
            "books_count": as_int(row.get("books_count")),
            "reasons": r,
            "settlement": row.get("settlement") if isinstance(row.get("settlement"), dict) else {},
        })
    audit = {
        "raw_artifact_rows": raw_rows,
        "merged_keys": len(merged),
        "line_less_rows_collapsed": collapsed,
        "line_less_rows_ambiguous": ambiguous,
        "excluded_coverage_only_rows": excluded_coverage_only,
        "prediction_rows": len(rows_out),
    }
    return rows_out, audit


def read_ledger_rows() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if not LEDGER.exists():
        return out
    for line in LEDGER.read_text(encoding="utf-8").splitlines():
        try:
            obj = json.loads(line)
            if isinstance(obj, dict):
                out.append(obj)
        except Exception:
            pass
    return out


def missing_core(row: dict[str, Any]) -> bool:
    return any(row.get(field) in (None, "") for field in CORE_METRIC_FIELDS)



def is_coverage_only_ledger_row(row: dict[str, Any]) -> bool:
    stages = set(str(x) for x in (row.get("stage_seen") or []))
    return bool(stages) and not bool(stages & PREDICTION_STAGES)


def prune_current_run_coverage_only(run_id: str) -> int:
    if not LEDGER.exists():
        return 0
    rows_existing = read_ledger_rows()
    kept = []
    removed = 0
    for row in rows_existing:
        if str(row.get("run_id") or "") == str(run_id) and is_coverage_only_ledger_row(row):
            removed += 1
        else:
            kept.append(row)
    if removed:
        with LEDGER.open("w", encoding="utf-8") as f:
            for row in kept:
                f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    return removed


def summarize(current_run_id: str, audit: dict[str, Any]) -> dict[str, Any]:
    rows_existing = read_ledger_rows()
    current = [r for r in rows_existing if str(r.get("run_id") or "") == str(current_run_id)]
    by_status = Counter(str(r.get("status") or "unknown") for r in rows_existing)
    by_reason = Counter()
    for row in rows_existing:
        for reason in row.get("reasons") or []:
            by_reason[str(reason)] += 1
    return {
        "status": "ok",
        "updated_at_utc": datetime.now(UTC).isoformat(),
        "ledger_path": str(LEDGER),
        "current_run_id": current_run_id,
        "total_rows": len(rows_existing),
        "current_run_rows": len(current),
        "rows_missing_core_metrics_total": sum(1 for r in rows_existing if missing_core(r)),
        "rows_missing_core_metrics_current_run": sum(1 for r in current if missing_core(r)),
        "by_status": dict(by_status),
        "top_reasons": by_reason.most_common(30),
        "accumulation_filter": audit,
        "notes": [
            "Use this ledger for forward testing before trusting ROI.",
            "api_coverage-only rows are excluded from the ledger; they belong in candidate-opportunity audit.",
            "quality/confidence are optional analytics fields; core metrics are identity + odds + EV + edge.",
        ],
    }


def main() -> int:
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    run_id = os.getenv("GITHUB_RUN_ID") or datetime.now(UTC).strftime("%Y%m%d%H%M%S")
    pruned = prune_current_run_coverage_only(run_id)
    seen = existing_ids()
    current_rows, audit = collect_current_rows()
    audit["pruned_existing_coverage_only_rows"] = pruned
    new_rows = [row for row in current_rows if row["ledger_id"] not in seen]
    if new_rows:
        with LEDGER.open("a", encoding="utf-8") as f:
            for row in new_rows:
                f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    summary = summarize(run_id, audit)
    summary["new_rows_added"] = len(new_rows)
    write_json(SUMMARY, summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
