from __future__ import annotations

"""Append run candidates to a forward-test ledger.

The ledger is the transition from patch-chasing to measurable forecasting:
every candidate that reaches discovery/quality/fallback is stored with its odds,
EV, reasons, publication status and later settlement fields if available.
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


def load_json(path: Path, default: Any) -> Any:
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


def flatten(row: dict[str, Any]) -> dict[str, Any]:
    metrics = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
    source_summary = row.get("source_summary") if isinstance(row.get("source_summary"), dict) else {}
    diagnostics = row.get("diagnostics") if isinstance(row.get("diagnostics"), dict) else {}
    out: dict[str, Any] = {}
    for src in (metrics, source_summary, diagnostics, row):
        for k, v in src.items():
            if v not in (None, "", [], {}):
                out.setdefault(k, v)
    return out


def rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if not isinstance(payload, dict):
        return []
    out: list[dict[str, Any]] = []
    for key in (
        "candidates",
        "rows",
        "data",
        "evaluated",
        "blocked_top",
        "near_miss",
        "selected",
        "selected_all",
        "published_candidates",
        "candidates_before_quality",
        "passed_candidates",
        "top_candidates",
        "sample",
        "rejected_samples",
    ):
        val = payload.get(key)
        if isinstance(val, list):
            out.extend(x for x in val if isinstance(x, dict))
        elif isinstance(val, dict):
            out.append(val)
    decision = payload.get("decision") if isinstance(payload.get("decision"), dict) else None
    if decision:
        out.extend(rows(decision))
    return out


def identity(row: dict[str, Any]) -> str:
    r = flatten(row)
    base = r.get("match_key") or f"{r.get('home_team')}|{r.get('away_team')}|{r.get('kickoff_utc') or r.get('commence_time')}"
    return "|".join([
        norm(base),
        norm(r.get("family") or r.get("market_family") or r.get("market")),
        norm(r.get("selection") or r.get("selection_key")),
        str(r.get("point") or r.get("line") or "").strip(),
    ])


def reasons(row: dict[str, Any]) -> list[str]:
    r = flatten(row)
    out: list[str] = []
    for name in ("reasons", "reject_reasons", "quality_reasons", "reject_reasons_ru"):
        val = r.get(name)
        if isinstance(val, list):
            out.extend(str(x) for x in val if str(x).strip())
        elif isinstance(val, str) and val.strip():
            out.extend(x.strip() for x in re.split(r"[,|;/]+", val) if x.strip())
    seen: set[str] = set()
    return [x for x in out if not (x in seen or seen.add(x))]


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


def load_run_log_status() -> dict[str, Any]:
    path = EXPORT_DIR / "latest-run-bot.log"
    text = ""
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        pass
    fatal_patterns = (
        "Traceback (most recent call last):",
        "runtime status 1",
        "SystemExit: 1",
    )
    # Non-fatal discovery warnings can contain RuntimeError text. Treat a run as
    # fatal only when there is a top-level traceback and no downstream fallback.
    fallback = load_json(EXPORT_DIR / "latest-controlled-fallback-report.json", {})
    has_fallback = bool(isinstance(fallback, dict) and (fallback.get("evaluated") or fallback.get("candidates_seen")))
    fatal = any(p in text for p in fatal_patterns) and not has_fallback
    return {"fatal": fatal, "has_fallback": has_fallback, "log_path": str(path)}


def collect_current_rows() -> list[dict[str, Any]]:
    sources = [
        ("before_quality", EXPORT_DIR / "latest-candidates-before-quality.json"),
        ("after_quality", EXPORT_DIR / "latest-candidates-after-quality.json"),
        ("api_coverage", EXPORT_DIR / "latest-api-coverage-consensus-runtime-patch.json"),
        ("quality_relief", EXPORT_DIR / "latest-quality-consensus-safe-relief.json"),
        ("fallback", EXPORT_DIR / "latest-controlled-fallback-report.json"),
        ("normalized_publication", EXPORT_DIR / "latest-normalized-publication-payloads.json"),
    ]
    merged: dict[str, dict[str, Any]] = {}
    stage_seen: dict[str, set[str]] = defaultdict(set)
    reason_seen: dict[str, list[str]] = defaultdict(list)
    for stage, path in sources:
        for row in rows(load_json(path, None)):
            k = identity(row)
            if not k.strip("| "):
                continue
            current = merged.setdefault(k, {})
            flat = flatten(row)
            # Preserve rich fields from fallback/normalized payloads, but allow
            # later artifacts to fill missing metrics.
            for kk, vv in flat.items():
                if vv not in (None, "", [], {}) and kk not in current:
                    current[kk] = vv
            # fallback is usually the most user-facing representation.
            if stage in {"fallback", "normalized_publication"}:
                for kk, vv in flat.items():
                    if vv not in (None, "", [], {}):
                        current[kk] = vv
            stage_seen[k].add(stage)
            reason_seen[k].extend(reasons(row))

    out = []
    run_id = os.getenv("GITHUB_RUN_ID") or datetime.now(UTC).strftime("%Y%m%d%H%M%S")
    for k, row in merged.items():
        r = []
        seen_reasons: set[str] = set()
        for item in reason_seen.get(k, []) + reasons(row):
            if item not in seen_reasons:
                seen_reasons.add(item)
                r.append(item)
        status = "published" if row.get("telegram_sent") is True or row.get("published") is True else "rejected_or_watch"
        if any("watch" in x.lower() for x in r):
            status = "watch_only"
        out.append({
            "ledger_id": f"{run_id}|{k}",
            "run_id": run_id,
            "created_at_utc": datetime.now(UTC).isoformat(),
            "candidate_key": k,
            "status": status,
            "stage_seen": sorted(stage_seen[k]),
            "match_key": row.get("match_key"),
            "home_team": row.get("home_team"),
            "away_team": row.get("away_team"),
            "league_name": row.get("league_name"),
            "kickoff_utc": row.get("kickoff_utc") or row.get("commence_time"),
            "family": row.get("family") or row.get("market_family") or row.get("market"),
            "selection": row.get("selection") or row.get("selection_key"),
            "point": row.get("point") or row.get("line"),
            "odds": as_float(row.get("odds") or row.get("selected_odds")),
            "ev_pct": as_float(row.get("canonical_ev_pct") or row.get("ev_pct") or row.get("ev")),
            "edge_pp": as_float(row.get("canonical_edge_pp") or row.get("edge_pct") or row.get("edge_pp")),
            "confidence": as_float(row.get("confidence")),
            "quality": as_float(row.get("quality") or row.get("quality_score")),
            "odds_sources_count": row.get("odds_sources_count") or row.get("independent_odds_sources_count"),
            "context_sources_count": row.get("context_sources_count") or row.get("confirmation_sources_count"),
            "books_count": row.get("books_count"),
            "reasons": r,
            "settlement": row.get("settlement") if isinstance(row.get("settlement"), dict) else {},
        })
    return out


def append_runtime_error_if_needed(new_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    status = load_run_log_status()
    if not status.get("fatal"):
        return new_rows
    run_id = os.getenv("GITHUB_RUN_ID") or datetime.now(UTC).strftime("%Y%m%d%H%M%S")
    new_rows.append({
        "ledger_id": f"{run_id}|runtime_error|runtime_error",
        "run_id": run_id,
        "created_at_utc": datetime.now(UTC).isoformat(),
        "candidate_key": "runtime_error",
        "status": "runtime_error",
        "stage_seen": ["run_once"],
        "match_key": None,
        "home_team": None,
        "away_team": None,
        "league_name": None,
        "kickoff_utc": None,
        "family": None,
        "selection": None,
        "point": None,
        "odds": None,
        "ev_pct": None,
        "edge_pp": None,
        "confidence": None,
        "quality": None,
        "odds_sources_count": None,
        "context_sources_count": None,
        "books_count": None,
        "reasons": ["runtime_error"],
        "settlement": {},
    })
    return new_rows


def row_missing_core_metrics(row: dict[str, Any]) -> bool:
    if row.get("status") == "runtime_error":
        return False
    return any(row.get(name) in (None, "") for name in ("home_team", "away_team", "family", "selection")) or all(
        row.get(name) is None for name in ("odds", "ev_pct", "edge_pp", "quality")
    )


def read_ledger_rows() -> list[dict[str, Any]]:
    rows_existing: list[dict[str, Any]] = []
    if LEDGER.exists():
        for line in LEDGER.read_text(encoding="utf-8").splitlines():
            try:
                obj = json.loads(line)
                if isinstance(obj, dict):
                    rows_existing.append(obj)
            except Exception:
                pass
    return rows_existing


def summarize(current_run_id: str = "") -> dict[str, Any]:
    rows_existing = read_ledger_rows()
    by_status = Counter(str(r.get("status") or "unknown") for r in rows_existing)
    by_reason: Counter[str] = Counter()
    for row in rows_existing:
        for reason in row.get("reasons") or []:
            by_reason[str(reason)] += 1

    current_rows = [r for r in rows_existing if str(r.get("run_id") or "") == str(current_run_id)] if current_run_id else []
    return {
        "status": "ok",
        "updated_at_utc": datetime.now(UTC).isoformat(),
        "ledger_path": str(LEDGER),
        "total_rows": len(rows_existing),
        "current_run_id": current_run_id,
        "current_run_rows": len(current_rows),
        "rows_missing_core_metrics_total": sum(1 for r in rows_existing if row_missing_core_metrics(r)),
        "rows_missing_core_metrics_current_run": sum(1 for r in current_rows if row_missing_core_metrics(r)),
        "by_status": dict(by_status),
        "top_reasons": by_reason.most_common(30),
        "notes": [
            "Use this ledger for forward testing before trusting ROI.",
            "Published/rejected/watch-only rows are accumulated; settlement fields can be filled by a future results job.",
            "rows_missing_core_metrics_current_run is the main quality signal; total may include older rows produced before ledger fixes.",
        ],
    }


def main() -> int:
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    run_id = os.getenv("GITHUB_RUN_ID") or datetime.now(UTC).strftime("%Y%m%d%H%M%S")
    seen = existing_ids()
    current = append_runtime_error_if_needed(collect_current_rows())
    new_rows = [r for r in current if r["ledger_id"] not in seen]
    if new_rows:
        with LEDGER.open("a", encoding="utf-8") as f:
            for row in new_rows:
                f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    summary = summarize(run_id)
    summary["new_rows_added"] = len(new_rows)
    write_json(SUMMARY, summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
