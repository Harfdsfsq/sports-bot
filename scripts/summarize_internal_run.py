from __future__ import annotations

import json
from pathlib import Path

ARTIFACTS = Path("artifacts")

def _load(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default

def main() -> int:
    integrity = _load(ARTIFACTS / "latest-candidate-integrity.json", {})
    publish = _load(ARTIFACTS / "canonical-publish-report.json", {})
    summary = {
        "candidates": int(integrity.get("total_candidates") or 0),
        "suspicious": int(integrity.get("suspicious_candidates") or 0),
        "published": bool(publish.get("published") or False),
        "picked_count": int(publish.get("picked_count") or 0),
    }
    (ARTIFACTS / "latest-internal-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
