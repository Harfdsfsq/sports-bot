from __future__ import annotations

import json
from pathlib import Path

from app.services.candidate_integrity import canonicalize_candidate_dict

ROOT = Path(".")
EXPORT_DIR = ROOT / ".data" / "exports"
ARTIFACTS_DIR = ROOT / "artifacts"


def _load_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _save_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    latest_picks = EXPORT_DIR / "latest-picks.json"
    latest_bets = EXPORT_DIR / "latest-bets.json"

    picks = _load_json(latest_picks, [])
    bets = _load_json(latest_bets, [])

    canonical_picks = [canonicalize_candidate_dict(dict(item)) for item in picks if isinstance(item, dict)]
    suspicious = [item for item in canonical_picks if str(item.get("integrity_status")) != "ok"]

    _save_json(ARTIFACTS_DIR / "latest-candidate-integrity.json", {
        "total_candidates": len(canonical_picks),
        "suspicious_candidates": len(suspicious),
        "candidates": canonical_picks,
    })
    _save_json(ARTIFACTS_DIR / "latest-canonical-picks.json", canonical_picks)

    if isinstance(bets, list) and bets:
        canonical_bets = [canonicalize_candidate_dict(dict(item)) for item in bets if isinstance(item, dict)]
        _save_json(ARTIFACTS_DIR / "latest-canonical-bets.json", canonical_bets)

    print(json.dumps({
        "ok": True,
        "canonical_candidates": len(canonical_picks),
        "suspicious_candidates": len(suspicious),
        "report_path": str(ARTIFACTS_DIR / "latest-candidate-integrity.json"),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
