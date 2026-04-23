from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def main() -> int:
    parser = argparse.ArgumentParser(description="Summarize the latest archived bot run.")
    parser.add_argument("--runs-root", default=".logs/runs")
    parser.add_argument("--output", default="artifacts/latest-run-summary.json")
    args = parser.parse_args()

    runs = sorted(Path(args.runs_root).glob("*/*-run.json"))
    if not runs:
        payload: dict[str, Any] = {"ok": False, "reason": "no_run_archives_found"}
    else:
        path = runs[-1]
        run = json.loads(path.read_text(encoding="utf-8"))
        summary = dict(run.get("summary") or {})
        payload = {
            "ok": True,
            "run_path": str(path),
            "created_at": run.get("created_at"),
            "published": summary.get("published"),
            "matches_seen": summary.get("matches_seen"),
            "matches_with_offers": summary.get("matches_with_offers"),
            "contexts_built": summary.get("contexts_built"),
            "candidates": summary.get("candidates"),
            "candidates_before_quality": summary.get("candidates_before_quality"),
            "candidates_rejected_by_quality": summary.get("candidates_rejected_by_quality"),
            "top_rejections": summary.get("top_rejections"),
            "top_quality_rejections": summary.get("top_quality_rejections"),
            "run_status": summary.get("run_status") or summary.get("status") or "unknown",
        }
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
