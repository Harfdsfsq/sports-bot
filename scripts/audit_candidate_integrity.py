from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from app.services.candidate_integrity import CandidateIntegrityService


def load_json(path: Path, default: Any) -> Any:
    try:
        if path.exists():
            text = path.read_text(encoding="utf-8")
            if text.strip():
                return json.loads(text)
    except Exception:
        pass
    return default


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def collect_candidates() -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []

    # Published picks exported by state/export layer.
    for path in (
        Path(".data/exports/latest-picks.json"),
        Path(".data/exports/latest-bets.json"),
    ):
        payload = load_json(path, [])
        if isinstance(payload, list):
            candidates.extend(item for item in payload if isinstance(item, dict))

    # Full run archive contains before-quality and after-quality candidate pools.
    debug = load_json(Path(".logs/debug-last-run.json"), {})
    if isinstance(debug, dict):
        for key in ("candidates", "candidates_before_quality", "publishable_candidates", "forecast_rows"):
            rows = debug.get(key)
            if isinstance(rows, list):
                candidates.extend(item for item in rows if isinstance(item, dict))

    # De-duplicate by stable identifiers when present.
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for item in candidates:
        key = "|".join(
            str(item.get(name) or "")
            for name in ("fingerprint", "match_key", "selection_key", "family", "point", "odds")
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


def main() -> int:
    implied_tolerance = float(os.getenv("PIPELINE_INTEGRITY_IMPLIED_TOLERANCE_PP", "2.0") or 2.0)
    adjusted_tolerance = float(os.getenv("PIPELINE_INTEGRITY_ADJUSTED_TOLERANCE_PP", "2.0") or 2.0)
    fair_ratio = float(os.getenv("PIPELINE_INTEGRITY_FAIR_ODDS_RATIO_MAX", "1.20") or 1.20)
    reject_neg_ev = str(os.getenv("PIPELINE_INTEGRITY_REJECT_ON_NEGATIVE_EDGE_POSITIVE_EV", "true")).lower() in {
        "1", "true", "yes", "on"
    }

    service = CandidateIntegrityService(
        implied_tolerance_pp=implied_tolerance,
        adjusted_tolerance_pp=adjusted_tolerance,
        fair_odds_ratio_max=fair_ratio,
        reject_negative_edge_positive_ev=reject_neg_ev,
    )

    candidates = collect_candidates()
    rows = []
    suspicious = 0
    for candidate in candidates:
        result = service.validate(candidate)
        if not result.ok:
            suspicious += 1
        rows.append(
            {
                "match_key": candidate.get("match_key"),
                "home_team": candidate.get("home_team"),
                "away_team": candidate.get("away_team"),
                "league_name": candidate.get("league_name"),
                "family": candidate.get("family"),
                "selection": candidate.get("selection"),
                "selection_key": candidate.get("selection_key"),
                "point": candidate.get("point"),
                "odds": candidate.get("odds"),
                "quality_status": (candidate.get("source_summary") or {}).get("quality_status") if isinstance(candidate.get("source_summary"), dict) else None,
                "ok": result.ok,
                "reasons": result.reasons,
                "integrity": result.as_dict(),
            }
        )

    payload = {
        "count": len(rows),
        "suspicious_candidates": suspicious,
        "strict": str(os.getenv("PIPELINE_INTEGRITY_STRICT", "true")).lower() in {"1", "true", "yes", "on"},
        "rows": rows,
    }
    write_json(Path(".data/exports/latest-candidate-integrity.json"), payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
