from __future__ import annotations

"""Repair publication ledger sync for controlled-fallback selected rows.

`sync_publication_ledger` already normalizes/merges bet rows, but its generic
container scanner reads list fields and misses the controlled fallback report's
`selected` dict / `selected_all` list shape.  When fallback publishes a Telegram
pick, this helper explicitly feeds those selected rows through the same semantic
ledger normalizer so settlement, daily reports and duplicate policy see the pick.
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from scripts import sync_publication_ledger as ledger

UTC = timezone.utc
ROOT = Path(".").resolve()
EXPORT_DIR = ROOT / ".data" / "exports"
REPORT_PATH = EXPORT_DIR / "latest-controlled-fallback-selected-ledger-sync.json"


def _load_json(path: Path, default: Any) -> Any:
    try:
        if path.exists() and path.stat().st_size > 0:
            return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        pass
    return default


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def selected_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    selected = payload.get("selected")
    if isinstance(selected, dict):
        rows.append(dict(selected))
    selected_all = payload.get("selected_all")
    if isinstance(selected_all, list):
        for item in selected_all:
            if isinstance(item, dict):
                rows.append(dict(item))
    out: list[dict[str, Any]] = []
    for row in rows:
        if not row:
            continue
        row["telegram_sent"] = True
        row["published"] = True
        row.setdefault("status", "pending")
        row.setdefault("publication_lifecycle_status", "telegram_sent")
        row.setdefault("publication_lifecycle_stage", "telegram_sent")
        row.setdefault("source", "controlled_fallback")
        row.setdefault("ledger_source_file", "latest-controlled-fallback-report.json:selected")
        row.setdefault("published_at_utc", payload.get("created_at") or payload.get("created_at_utc") or datetime.now(UTC).isoformat())
        out.append(row)
    return out


def main() -> int:
    fallback = _load_json(EXPORT_DIR / "latest-controlled-fallback-report.json", {})
    if not isinstance(fallback, dict) or not fallback.get("published"):
        report = {"status": "skipped", "reason": "no_published_controlled_fallback", "created_at_utc": datetime.now(UTC).isoformat()}
        _write_json(REPORT_PATH, report)
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    selected = selected_rows(fallback)
    existing = ledger.iter_jsonl(ledger.PUBLISHED_JSONL) + ledger.iter_jsonl(ledger.SETTLED_JSONL)
    existing.extend([x for x in _load_json(EXPORT_DIR / "latest-picks.json", []) if isinstance(x, dict)])
    rows = [ledger.normalize_bet(row) for row in selected]
    merged, stats = ledger.merge_by_key(existing, rows)
    pending, pending_stats = ledger.merge_by_key([], [row for row in merged if ledger.is_pending(row)])
    settled, settled_stats = ledger.merge_by_key([], [row for row in merged if not ledger.is_pending(row)])

    if merged:
        ledger.write_jsonl(ledger.PUBLISHED_JSONL, merged)
        ledger.write_json(ledger.PENDING_JSON, pending)
        ledger.write_jsonl(ledger.SETTLED_JSONL, settled)
        ledger.write_json(EXPORT_DIR / "latest-pending-bets.json", pending)
        ledger.write_json(EXPORT_DIR / "latest-picks.json", merged)
        ledger.write_json(EXPORT_DIR / "latest-settled-bets.json", settled)
        # The legacy report reader still uses latest-bets.json in some paths.
        ledger.write_json(EXPORT_DIR / "latest-bets.json", pending)

    state_stats = ledger.mirror_to_state(merged) if merged else {"state_bets": 0, "state_added": 0, "state_updated": 0, "state_open_exposure": 0.0}
    report = {
        "status": "ok",
        "created_at_utc": datetime.now(UTC).isoformat(),
        "selected_rows_seen": len(selected),
        "rows_added_to_merge": len(rows),
        "published_ledger_rows": len(merged),
        "pending_unique_rows": pending_stats.get("unique_rows", len(pending)),
        "settled_unique_rows": settled_stats.get("unique_rows", len(settled)),
        "duplicates_removed": stats.get("duplicates_removed", 0),
        "state_mirror": state_stats,
        "selected_keys": [row.get("ledger_semantic_key") or row.get("dedupe_key") for row in rows],
    }
    _write_json(REPORT_PATH, report)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
