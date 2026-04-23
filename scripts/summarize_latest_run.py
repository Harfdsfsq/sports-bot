from __future__ import annotations

import json
from pathlib import Path

def main() -> int:
    debug = Path(".logs/debug-last-run.json")
    payload = {}
    if debug.exists():
        try:
            payload = json.loads(debug.read_text(encoding="utf-8"))
        except Exception as exc:
            payload = {"read_error": f"{type(exc).__name__}: {exc}"}
    summary = {
        "created_at": payload.get("created_at"),
        "summary": payload.get("summary") or {},
        "error": payload.get("error"),
    }
    out = Path("artifacts/latest-run-summary.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
