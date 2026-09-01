from __future__ import annotations

"""Run all-time predictions report with fallback sent-index import.

send_all_time_predictions_report.py calls sync_publication_ledger at startup.
That sync can overwrite a prior manual import because older sync versions do not
read .data/fallback-sent-index.json directly.  This wrapper preserves the normal
sync, immediately imports the fallback sent-index, and patches tier labels so
Russian labels like 'уровень B' are grouped as B instead of '?'.
"""

import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import send_all_time_predictions_report as report

IMPORT_REPORT = ROOT / ".data" / "exports" / "latest-fallback-sent-index-ledger-import.json"


def load_json(path: Path, default: Any) -> Any:
    try:
        if path.exists() and path.stat().st_size > 0:
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return default


def normalized_tier(row: dict[str, Any]) -> str:
    values = [
        row.get("tier"),
        row.get("publication_tier"),
        report.nested(row, "source_summary").get("tier"),
    ]
    for value in values:
        text = str(value or "").strip().upper()
        if text in {"A", "B", "C"}:
            return text
        # Covers labels such as 'уровень B', 'B-tier', 'tier_b'.
        match = re.search(r"(?:^|[^A-Z])([ABC])(?:[^A-Z]|$)", text)
        if match:
            return match.group(1)
    quality = report.quality(row)
    if quality in {"raw", "a_cover_evidence"}:
        return "A"
    if quality == "controlled_fallback":
        return "B"
    return "?"


def sync_ledger_then_import_sent_index() -> dict[str, Any]:
    base_sync = report.sync_ledger_original() if hasattr(report, "sync_ledger_original") else report._original_sync_ledger()
    import_payload: dict[str, Any]
    try:
        from scripts import import_fallback_sent_index_to_ledger
        import_fallback_sent_index_to_ledger.main()
        import_payload = load_json(IMPORT_REPORT, {})
    except Exception as exc:
        import_payload = {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
    return {
        "sync_publication_ledger": base_sync,
        "fallback_sent_index_import_after_sync": import_payload,
    }


def main() -> int:
    if not hasattr(report, "_original_sync_ledger"):
        report._original_sync_ledger = report.sync_ledger  # type: ignore[attr-defined]
    report.sync_ledger_original = report._original_sync_ledger  # type: ignore[attr-defined]
    report.sync_ledger = sync_ledger_then_import_sent_index  # type: ignore[assignment]
    report.tier = normalized_tier  # type: ignore[assignment]
    return int(report.main() or 0)


if __name__ == "__main__":
    raise SystemExit(main())
