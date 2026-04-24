from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_json(path: Path, default: Any) -> Any:
    try:
        if path.exists() and path.read_text(encoding="utf-8").strip():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return default


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    debug = load_json(Path(".logs/debug-last-run.json"), {})
    summary = dict(debug.get("summary") or {}) if isinstance(debug, dict) else {}
    integrity = load_json(Path(".data/exports/latest-candidate-integrity.json"), {})
    picks = load_json(Path(".data/exports/latest-picks.json"), [])
    quality = load_json(Path(".data/exports/latest-quality-report.json"), {})

    payload = {
        "created_at": debug.get("created_at") if isinstance(debug, dict) else None,
        "matches_seen": summary.get("matches_seen"),
        "matches_before_publish_window": summary.get("matches_before_publish_window"),
        "matches_with_offers": summary.get("matches_with_offers"),
        "contexts_built": summary.get("contexts_built"),
        "candidates_before_quality": summary.get("candidates_before_quality"),
        "candidates": summary.get("candidates"),
        "candidates_publishable": summary.get("candidates_publishable"),
        "published": summary.get("published"),
        "published_to_telegram": summary.get("published_to_telegram"),
        "dry_run": summary.get("dry_run"),
        "prediction_publication_enabled": summary.get("prediction_publication_enabled"),
        "run_rejections_top": dict(list((summary.get("rejections") or {}).items())[:20]) if isinstance(summary.get("rejections"), dict) else {},
        "integrity": {
            "count": integrity.get("count") if isinstance(integrity, dict) else None,
            "suspicious_candidates": integrity.get("suspicious_candidates") if isinstance(integrity, dict) else None,
        },
        "picks_count": len(picks) if isinstance(picks, list) else None,
        "quality_summary": (quality.get("summary") or {}) if isinstance(quality, dict) else {},
    }
    write_json(Path("artifacts/run-bot/latest-run-summary.json"), payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
