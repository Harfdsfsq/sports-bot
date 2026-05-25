from __future__ import annotations

"""Build a before/after calibration audit for every candidate row we can see.

This is an analytics-only artifact.  It merges candidate rows from discovery,
quality, API-coverage and controlled fallback artifacts into one row per logical
candidate so the report can explain *why* a candidate disappeared without losing
team names, odds or metrics that are present in a different artifact.
"""

import json
import os
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

UTC = timezone.utc
ROOT = Path(".").resolve()
EXPORT_DIR = ROOT / ".data" / "exports"
OUT = EXPORT_DIR / "latest-prediction-calibration-audit.json"


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def as_float(value: Any, default: float | None = None) -> float | None:
    try:
        if value in (None, ""):
            return default
        return float(str(value).replace(",", "."))
    except Exception:
        return default


def norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def candidate_rows(payload: Any) -> list[dict[str, Any]]:
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
        "sample",
        "selected",
        "selected_all",
        "published_candidates",
        "candidates_before_quality",
        "passed_candidates",
        "top_candidates",
        "rejected_samples",
    ):
        val = payload.get(key)
        if isinstance(val, list):
            out.extend(x for x in val if isinstance(x, dict))
        elif isinstance(val, dict):
            out.append(val)
    decision = payload.get("decision") if isinstance(payload.get("decision"), dict) else None
    if decision:
        out.extend(candidate_rows(decision))
    return out


def nested(row: dict[str, Any]) -> dict[str, Any]:
    metrics = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
    source_summary = row.get("source_summary") if isinstance(row.get("source_summary"), dict) else {}
    diagnostics = row.get("diagnostics") if isinstance(row.get("diagnostics"), dict) else {}
    merged: dict[str, Any] = {}
    # Nested values are fallback candidates' source of truth, but top-level names
    # should win when present because quality/export patches may flatten fields.
    for src in (metrics, source_summary, diagnostics, row):
        for k, v in src.items():
            if v not in (None, "", [], {}):
                merged.setdefault(k, v)
    return merged


def key(row: dict[str, Any]) -> str:
    r = nested(row)
    base = r.get("match_key") or f"{r.get('home_team')}|{r.get('away_team')}|{r.get('kickoff_utc') or r.get('commence_time')}"
    return "|".join([
        norm(base),
        norm(r.get("family") or r.get("market_family") or r.get("market")),
        norm(r.get("selection") or r.get("selection_key")),
        str(r.get("point") or r.get("line") or "").strip(),
    ])


def reasons(row: dict[str, Any]) -> list[str]:
    r = nested(row)
    out: list[str] = []
    for name in ("reasons", "reject_reasons", "quality_reasons", "reject_reasons_ru"):
        val = r.get(name)
        if isinstance(val, list):
            out.extend(str(x) for x in val if str(x).strip())
        elif isinstance(val, str) and val.strip():
            out.extend(x.strip() for x in re.split(r"[,|;/]+", val) if x.strip())
    # stable order, no duplicates
    seen: set[str] = set()
    uniq: list[str] = []
    for item in out:
        if item not in seen:
            seen.add(item)
            uniq.append(item)
    return uniq


def collect(path: Path) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for row in candidate_rows(load_json(path, None)):
        row = dict(row)
        row["_artifact"] = path.name
        k = key(row)
        if not k.strip("| "):
            continue
        out.setdefault(k, row)
    return out


def merge_candidate_rows(*rows_: dict[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    all_reasons: list[str] = []
    stage_artifacts: list[str] = []
    for row in rows_:
        if not row:
            continue
        flat = nested(row)
        artifact = row.get("_artifact")
        if artifact:
            stage_artifacts.append(str(artifact))
        for k, v in flat.items():
            if v not in (None, "", [], {}):
                # Do not overwrite meaningful names/odds with later sparse rows.
                merged.setdefault(k, v)
        all_reasons.extend(reasons(row))
    if all_reasons:
        seen: set[str] = set()
        merged["all_reasons"] = [x for x in all_reasons if not (x in seen or seen.add(x))]
    if stage_artifacts:
        merged["stage_artifacts"] = sorted(set(stage_artifacts))
    return merged


def pick_float(row: dict[str, Any], *names: str) -> float | None:
    for name in names:
        value = row.get(name)
        if value not in (None, ""):
            return as_float(value)
    return None


def main() -> int:
    before = collect(EXPORT_DIR / "latest-candidates-before-quality.json")
    after = collect(EXPORT_DIR / "latest-candidates-after-quality.json")
    fallback = collect(EXPORT_DIR / "latest-controlled-fallback-report.json")
    quality = collect(EXPORT_DIR / "latest-quality-consensus-safe-relief.json")
    api = collect(EXPORT_DIR / "latest-api-coverage-consensus-runtime-patch.json")
    value_patch = collect(EXPORT_DIR / "latest-candidate-value-runtime-patch.json")

    all_keys = sorted(set(before) | set(after) | set(fallback) | set(quality) | set(api) | set(value_patch))
    rows_out: list[dict[str, Any]] = []
    reason_counts: Counter[str] = Counter()
    negative_after = 0
    rows_missing_core_metrics = 0

    for k in all_keys:
        b = merge_candidate_rows(before.get(k, {}), value_patch.get(k, {}))
        a = merge_candidate_rows(api.get(k, {}), quality.get(k, {}), after.get(k, {}), fallback.get(k, {}))
        merged = merge_candidate_rows(before.get(k, {}), value_patch.get(k, {}), api.get(k, {}), quality.get(k, {}), after.get(k, {}), fallback.get(k, {}))

        ev_before = pick_float(b, "ev_pct", "ev", "canonical_ev_pct", "canonical_ev")
        ev_after = pick_float(a, "canonical_ev_pct", "ev_pct", "ev", "canonical_ev")
        edge_before = pick_float(b, "edge_pct", "edge_pp", "canonical_edge_pp")
        edge_after = pick_float(a, "canonical_edge_pp", "edge_pct", "edge_pp")
        r = merged.get("all_reasons") if isinstance(merged.get("all_reasons"), list) else []
        for item in r:
            reason_counts[str(item)] += 1
        negative_after += int(ev_after is not None and ev_after < 0)

        core_missing = any(
            merged.get(name) in (None, "")
            for name in ("home_team", "away_team")
        ) or all(v is None for v in (ev_after, edge_after, pick_float(merged, "odds", "selected_odds")))
        rows_missing_core_metrics += int(core_missing)

        rows_out.append({
            "key": k,
            "match_key": merged.get("match_key"),
            "home_team": merged.get("home_team"),
            "away_team": merged.get("away_team"),
            "league_name": merged.get("league_name"),
            "kickoff_utc": merged.get("kickoff_utc") or merged.get("commence_time"),
            "family": merged.get("family") or merged.get("market_family") or merged.get("market"),
            "selection": merged.get("selection") or merged.get("selection_key"),
            "point": merged.get("point") or merged.get("line"),
            "odds": pick_float(merged, "odds", "selected_odds"),
            "ev_before_pct": ev_before,
            "ev_after_pct": ev_after,
            "edge_before_pp": edge_before,
            "edge_after_pp": edge_after,
            "ev_delta_pct": round(ev_after - ev_before, 4) if ev_before is not None and ev_after is not None else None,
            "edge_delta_pp": round(edge_after - edge_before, 4) if edge_before is not None and edge_after is not None else None,
            "confidence": pick_float(merged, "confidence"),
            "quality": pick_float(merged, "quality", "quality_score"),
            "odds_sources_count": merged.get("odds_sources_count") or merged.get("independent_odds_sources_count"),
            "context_sources_count": merged.get("context_sources_count") or merged.get("confirmation_sources_count"),
            "books_count": merged.get("books_count"),
            "reasons": r,
            "stage_seen": {
                "before_quality": k in before,
                "after_quality": k in after,
                "fallback": k in fallback,
                "quality_relief": k in quality,
                "api_coverage": k in api,
                "value_patch": k in value_patch,
            },
            "stage_artifacts": merged.get("stage_artifacts", []),
            "missing_core_metrics": core_missing,
        })

    payload = {
        "status": "ok",
        "created_at_utc": datetime.now(UTC).isoformat(),
        "run_id": os.getenv("GITHUB_RUN_ID") or "",
        "counts": {
            "candidate_keys": len(all_keys),
            "before_quality": len(before),
            "after_quality": len(after),
            "fallback": len(fallback),
            "quality_relief": len(quality),
            "api_coverage": len(api),
            "value_patch": len(value_patch),
            "negative_ev_after_calibration": negative_after,
            "rows_missing_core_metrics": rows_missing_core_metrics,
        },
        "top_reasons": reason_counts.most_common(30),
        "rows": rows_out[:300],
        "notes": [
            "This is an audit only. It does not change candidate probabilities, EV, or publication guards.",
            "Rows merge discovery, API coverage, quality and fallback artifacts so metrics are not lost when one layer exports sparse data.",
        ],
    }
    write_json(OUT, payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
