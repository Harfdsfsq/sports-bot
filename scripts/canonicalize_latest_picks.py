from __future__ import annotations

import json
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.services.candidate_integrity import canonicalize_candidate, load_latest_picks, write_json  # noqa: E402


def main() -> int:
    picks = load_latest_picks(REPO_ROOT)
    canonical = [canonicalize_candidate(item) for item in picks]
    suspicious = [item for item in canonical if bool(((item.get("candidate_integrity") or {}).get("is_suspicious")))]
    out_dir = REPO_ROOT / "artifacts/fixed-run"
    write_json(out_dir / "latest-canonical-picks.json", canonical)
    write_json(
        out_dir / "latest-candidate-integrity.json",
        {
            "total_candidates": len(canonical),
            "suspicious_candidates": len(suspicious),
            "items": [
                {
                    "match_key": item.get("match_key"),
                    "family": item.get("family"),
                    "selection": item.get("selection"),
                    "point": item.get("point"),
                    "odds": item.get("canonical_selected_odds"),
                    "candidate_integrity": item.get("candidate_integrity"),
                }
                for item in suspicious
            ],
        },
    )
    print(json.dumps({"canonical_candidates": len(canonical), "suspicious_candidates": len(suspicious)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
