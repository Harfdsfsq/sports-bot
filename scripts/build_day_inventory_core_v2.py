from __future__ import annotations

"""Safe wrapper for build_day_inventory_core.

The first core-builder run failed late with `NameError: Counter is not defined`.
This wrapper injects the missing Counter symbol into the implementation module
and guarantees a JSON failure summary if a future unexpected exception happens.
"""

import asyncio
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

UTC = timezone.utc
EXPORT_DIR = Path(".data/exports")
SUMMARY_PATH = EXPORT_DIR / "latest-day-inventory-summary.json"
CORE_REPORT_PATH = EXPORT_DIR / "latest-day-inventory-core-build-report.json"


def _write_failure(error: BaseException) -> None:
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "build_status": "error",
        "mode": "core_provider_discovery_top300",
        "error": f"{type(error).__name__}: {error}",
        "created_at_utc": datetime.now(UTC).isoformat(),
        "counts": {
            "matches_total": 0,
            "matches_after_top_cut": 0,
            "matches_raw_before_merge": 0,
            "matches_after_core_merge": 0,
        },
    }
    SUMMARY_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    CORE_REPORT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def main() -> int:
    import scripts.build_day_inventory_core as core

    # Fix missing global used by core.enrich_payload_coverage().
    core.Counter = Counter

    try:
        return int(core.main() or 0)
    except SystemExit as exc:
        code = exc.code
        if isinstance(code, int):
            return code
        return 0
    except Exception as exc:
        _write_failure(exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
