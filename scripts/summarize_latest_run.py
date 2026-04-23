from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(".")
DEBUG = ROOT / ".logs" / "debug-last-run.json"
STATE = ROOT / ".data" / "state.json"

def load_json(path: Path):
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}

def main() -> int:
    debug = load_json(DEBUG)
    state = load_json(STATE)
    last_run = dict(state.get("last_run") or {})
    run_history = list(state.get("run_history") or [])
    learning_state = dict(state.get("learning_state") or {})
    summary = {
        "last_run_status": last_run.get("status"),
        "last_run_at": last_run.get("at"),
        "last_run_summary": last_run.get("summary") or {},
        "run_history_count": len(run_history),
        "learning_updated_at": learning_state.get("updated_at"),
        "learning_report_date": learning_state.get("report_date"),
        "top_failure_tags": (((learning_state.get("error_analysis") or {}).get("top_failure_tags")) or {}),
        "debug_keys": sorted(debug.keys())[:80] if isinstance(debug, dict) else [],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
