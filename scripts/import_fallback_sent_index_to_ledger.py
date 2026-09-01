from __future__ import annotations

"""Import the persistent fallback sent index into the durable bets ledger.

The fallback sent index is the most reliable record of controlled-fallback
Telegram publications.  It can contain picks that were sent before the durable
.data/bets ledger existed, so the manual performance report must merge it before
settlement/reporting.
"""

import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import sync_publication_ledger as ledger

DATA = ROOT / ".data"
EXPORT = DATA / "exports"
REPORT = EXPORT / "latest-fallback-sent-index-ledger-import.json"

SOURCES = [
    DATA / "fallback-sent-index.json",
    DATA / "publication-sent-index.json",
    DATA / "fallback_sent_index.json",
    EXPORT / "latest-fallback-sent-index.json",
    EXPORT / "fallback-sent-index.json",
    EXPORT / "latest-publication-sent-index.json",
]


def load_json(path: Path, default: Any) -> Any:
    try:
        if path.exists() and path.stat().st_size > 0:
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return default


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def iter_index_rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if not isinstance(payload, dict):
        return []
    rows: list[dict[str, Any]] = []
    values_are_rows = 0
    for value in payload.values():
        if isinstance(value, dict) and any(k in value for k in ("home_team", "away_team", "match_name", "canonical_publication_key")):
            values_are_rows += 1
    if values_are_rows:
        return [dict(x) for x in payload.values() if isinstance(x, dict)]
    for key in ("rows", "items", "bets", "picks", "sent", "published", "published_picks", "sent_picks"):
        value = payload.get(key)
        if isinstance(value, list):
            rows.extend([x for x in value if isinstance(x, dict)])
        elif isinstance(value, dict):
            rows.extend([x for x in value.values() if isinstance(x, dict)])
    return rows


def blank_or_unknown(value: Any) -> bool:
    return str(value or "").strip().upper() in {"", "?", "UNKNOWN", "NONE", "NULL"}


def mark_sent(row: dict[str, Any], source: str) -> dict[str, Any]:
    out = dict(row)
    out["telegram_sent"] = True
    out["published"] = True
    out.setdefault("status", "pending")
    out.setdefault("publication_lifecycle_status", "telegram_sent")
    out.setdefault("publication_lifecycle_stage", "telegram_sent")
    out.setdefault("source", "fallback_sent_index_import")
    out.setdefault("published_by", "controlled_fallback")
    out.setdefault("ledger_source_file", source)

    if blank_or_unknown(out.get("tier")):
        out["tier"] = out.get("publication_tier") if not blank_or_unknown(out.get("publication_tier")) else "B"
    if blank_or_unknown(out.get("publication_tier")):
        out["publication_tier"] = out.get("tier") if not blank_or_unknown(out.get("tier")) else "B"
    if blank_or_unknown(out.get("quality_source")):
        out["quality_source"] = out.get("quality_score_source") if not blank_or_unknown(out.get("quality_score_source")) else "controlled_fallback"
    if blank_or_unknown(out.get("quality_score_source")):
        out["quality_score_source"] = out.get("quality_source") if not blank_or_unknown(out.get("quality_source")) else "controlled_fallback"
    summary = out.get("source_summary") if isinstance(out.get("source_summary"), dict) else {}
    if blank_or_unknown(summary.get("tier")):
        summary["tier"] = out.get("tier") or "B"
    if blank_or_unknown(summary.get("quality_source")):
        summary["quality_source"] = out.get("quality_source") or "controlled_fallback"
    summary.setdefault("publication_mode", "controlled_fallback")
    out["source_summary"] = summary

    sent_at = out.get("sent_at") or out.get("published_at") or out.get("published_at_utc")
    if sent_at:
        out.setdefault("published_at_utc", sent_at)
    if out.get("match_name") and (not out.get("home_team") or not out.get("away_team")):
        parts = str(out.get("match_name") or "").replace("—", "-").split("-", 1)
        if len(parts) == 2:
            out.setdefault("home_team", parts[0].strip())
            out.setdefault("away_team", parts[1].strip())
    return out


def main() -> int:
    EXPORT.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    source_stats: list[dict[str, Any]] = []
    for path in SOURCES:
        payload = load_json(path, None)
        if payload is None:
            continue
        source_rows = [mark_sent(x, str(path)) for x in iter_index_rows(payload)]
        rows.extend(source_rows)
        source_stats.append({"path": str(path), "rows": len(source_rows)})

    existing = ledger.iter_jsonl(ledger.PUBLISHED_JSONL) + ledger.iter_jsonl(ledger.SETTLED_JSONL)
    merged, merge_stats = ledger.merge_by_key(existing, rows)
    pending, pending_stats = ledger.merge_by_key([], [r for r in merged if ledger.is_pending(r)])
    settled, settled_stats = ledger.merge_by_key([], [r for r in merged if not ledger.is_pending(r)])

    ledger.write_jsonl(ledger.PUBLISHED_JSONL, merged)
    ledger.write_json(ledger.PENDING_JSON, pending)
    ledger.write_jsonl(ledger.SETTLED_JSONL, settled)
    ledger.write_json(ledger.EXPORT_DIR / "latest-pending-bets.json", pending)
    ledger.write_json(ledger.EXPORT_DIR / "latest-picks.json", merged)
    ledger.write_json(ledger.EXPORT_DIR / "latest-settled-bets.json", settled)
    state_stats = ledger.mirror_to_state(merged)

    payload = {
        "status": "ok",
        "created_at_utc": datetime.now(UTC).isoformat(),
        "sources": source_stats,
        "rows_from_sent_index": len(rows),
        "published_ledger_rows": len(merged),
        "pending_rows": len(pending),
        "settled_rows": len(settled),
        "duplicates_removed": merge_stats.get("duplicates_removed", 0),
        "pending_duplicates_removed": pending_stats.get("duplicates_removed", 0),
        "settled_duplicates_removed": settled_stats.get("duplicates_removed", 0),
        "state_mirror": state_stats,
        "sample": [
            {
                "home_team": r.get("home_team"),
                "away_team": r.get("away_team"),
                "selection_key": r.get("selection_key"),
                "point": r.get("point"),
                "odds": r.get("odds"),
                "sent_at": r.get("sent_at"),
                "tier": r.get("tier"),
                "quality_source": r.get("quality_source"),
            }
            for r in rows[:15]
        ],
    }
    write_json(REPORT, payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
