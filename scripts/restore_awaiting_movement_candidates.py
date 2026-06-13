from __future__ import annotations

"""Restore awaiting movement candidates into the rescue pool before fallback.

This makes candidates saved on a previous run visible to the controlled fallback
review after their next line snapshot has been collected.
"""

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(".").resolve()
STATE = ROOT / ".data" / "candidate-lifecycle-state.json"
RESCUE = ROOT / ".data" / "exports" / "latest-rescue-candidates.json"
OUT = ROOT / ".data" / "exports" / "latest-awaiting-movement-restore.json"
UTC = timezone.utc


def _load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _parse_dt(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        text = str(value).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)
    except Exception:
        return None


def _norm(value: Any) -> str:
    text = str(value or "").strip().lower().replace("ё", "е")
    text = re.sub(r"[^a-z0-9а-я]+", " ", text)
    return " ".join(text.split())


def _point(value: Any) -> str:
    try:
        f = float(str(value).replace(",", "."))
        return str(int(f)) if f.is_integer() else f"{f:g}"
    except Exception:
        return _norm(value)


def _sig(row: dict[str, Any]) -> str:
    match = _norm(row.get("match_key") or row.get("canonical_match_id") or row.get("event_key"))
    if not match:
        match = f"{_norm(row.get('home_team') or row.get('home'))}|{_norm(row.get('away_team') or row.get('away'))}|{str(row.get('commence_time') or row.get('kickoff') or '')[:10]}"
    selection = _norm(row.get("selection_key") or row.get("selection"))
    if "меньше" in selection or "under" in selection or "тм" in selection:
        selection = "under"
    elif "больше" in selection or "over" in selection or "тб" in selection:
        selection = "over"
    return "|".join([match, _norm(row.get("family") or row.get("market_family")), selection, _point(row.get("point") or row.get("line") or row.get("handicap"))])


def _extract_rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if isinstance(payload, dict):
        for key in ("candidates", "items", "rows", "selected_all"):
            value = payload.get(key)
            if isinstance(value, list):
                return [x for x in value if isinstance(x, dict)]
    return []


def _write_rescue(rows: list[dict[str, Any]]) -> None:
    # Preserve the list shape when possible because the fallback reader accepts it.
    _write_json(RESCUE, rows)


def main() -> int:
    now = datetime.now(UTC)
    state = _load_json(STATE, {})
    awaiting = state.get("awaiting_movement_candidates") if isinstance(state, dict) else []
    if not isinstance(awaiting, list):
        awaiting = []
    existing_payload = _load_json(RESCUE, [])
    rescue_rows = _extract_rows(existing_payload)
    by_sig = {_sig(row): row for row in rescue_rows if isinstance(row, dict)}

    restored = 0
    skipped = {"expired": 0, "too_far": 0, "bad_row": 0}
    for row in awaiting:
        if not isinstance(row, dict):
            skipped["bad_row"] += 1
            continue
        kickoff = _parse_dt(row.get("commence_time") or row.get("kickoff") or row.get("start_time"))
        if kickoff is not None and kickoff < now:
            skipped["expired"] += 1
            continue
        if kickoff is not None and kickoff > now + timedelta(hours=24):
            skipped["too_far"] += 1
            continue
        item = dict(row)
        item["_candidate_source"] = "awaiting_movement_lifecycle"
        item["publication_lifecycle_status"] = "awaiting_next_run_movement_check"
        sig = _sig(item)
        if sig not in by_sig:
            restored += 1
        by_sig[sig] = item

    merged = list(by_sig.values())
    _write_rescue(merged)
    payload = {
        "status": "ok",
        "created_at_utc": now.isoformat(),
        "awaiting_seen": len(awaiting),
        "existing_rescue_rows": len(rescue_rows),
        "restored": restored,
        "rescue_rows_after": len(merged),
        "skipped": skipped,
        "sample": [x for x in merged if x.get("_candidate_source") == "awaiting_movement_lifecycle"][:10],
    }
    _write_json(OUT, payload)
    print(json.dumps({"status": "ok", "restored": restored, "rescue_rows_after": len(merged)}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
