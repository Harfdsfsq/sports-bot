from __future__ import annotations

import json
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.services.candidate_integrity import canonicalize_candidate, load_latest_picks, write_json  # noqa: E402


def main() -> int:
    picks = [canonicalize_candidate(item) for item in load_latest_picks(REPO_ROOT)]
    suspicious = []
    passed = []
    for item in picks:
        integrity = dict(item.get("candidate_integrity") or {})
        if integrity.get("is_suspicious"):
            suspicious.append(item)
            if str(((item.get("source_summary") or {}).get("quality_status") or "")).startswith("passed"):
                passed.append(item)
    report = {
        "total_candidates": len(picks),
        "suspicious_candidates": len(suspicious),
        "published_or_passed_suspicious_candidates": len(passed),
        "items": [
            {
                "match_key": item.get("match_key"),
                "family": item.get("family"),
                "selection": item.get("selection"),
                "point": item.get("point"),
                "odds": item.get("canonical_selected_odds"),
                "selected_bookmaker": (item.get("source_summary") or {}).get("selected_bookmaker"),
                "quality_status": (item.get("source_summary") or {}).get("quality_status"),
                "reasons": (item.get("candidate_integrity") or {}).get("reasons") or [],
            }
            for item in suspicious
        ],
    }
    out = REPO_ROOT / "artifacts/fixed-run/latest-odds-integrity-report.json"
    write_json(out, report)
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
