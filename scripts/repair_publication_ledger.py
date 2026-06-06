from __future__ import annotations

"""One-shot HARIZON ledger repair/dedupe utility.

Safe to run before or after normal ledger sync.  It removes duplicate rows for
one Telegram bet across .data/bets and legacy .data/exports snapshots by using a
semantic key: match + kickoff + market family + selection + point.
"""

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

EXPORT_DIR = Path(".data/exports")
STATUS_PATH = EXPORT_DIR / "latest-publication-ledger-repair.json"


def _write_status(payload: dict[str, Any]) -> None:
    STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATUS_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> int:
    started = datetime.now(timezone.utc).isoformat()
    try:
        # The durable repair logic lives in sync_publication_ledger.py so the
        # workflow has exactly one implementation of semantic bet dedupe.
        result = subprocess.run(
            [sys.executable, "scripts/sync_publication_ledger.py", "--phase", "repair-ledger"],
            text=True,
            capture_output=True,
            timeout=60,
        )
        payload: dict[str, Any] = {
            "status": "ok" if result.returncode == 0 else "sync_failed",
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "started_at_utc": started,
            "returncode": result.returncode,
            "stdout_tail": result.stdout[-4000:],
            "stderr_tail": result.stderr[-4000:],
            "dedupe_policy": "semantic_match_market_selection_point_kickoff",
        }
        _write_status(payload)
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if result.returncode == 0 else result.returncode
    except Exception as exc:
        payload = {
            "status": "error",
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "started_at_utc": started,
            "error": repr(exc),
        }
        _write_status(payload)
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
