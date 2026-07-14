from __future__ import annotations

"""Restore awaiting movement candidates into the rescue pool before fallback.

Awaiting rows can be discovered many hours before kick-off.  The fallback pool is
only allowed to publish inside the configured window, so restoring every 24h row
creates noisy ``latest_rescue_candidates_stale_or_outside_window`` counters and
can make the report say candidates were lost even though they are simply too far
away.  Keep the lifecycle state intact, but only write rows into
``latest-rescue-candidates.json`` when they are inside the active fallback scan
window.
"""

import json
import os
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


def _env_int(name: str, default: int) -> int:
    try:
        raw = os.getenv(name)
        return int(float(str(raw).strip())) if raw not in (None, "") else default
    except Exception:
        return default


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


def _date_from_any(*values: Any) -> str:
    for value in values:
        match = re.search(r"20\d{2}-\d{2}-\d{2}", str(value or ""))
        if match:
            return match.group(0)
    return ""


def _key_parts(raw: str) -> tuple[str, str, str] | None:
    parts = [part for part in str(raw or "").split("|") if part]
    if len(parts) >= 4 and parts[0].lower() == "soccer":
        return parts[1], parts[2], parts[3]
    if len(parts) >= 3 and re.match(r"20\d{2}-\d{2}-\d{2}", parts[0]):
        return parts[1], parts[2], parts[0]
    return None


def _match_key(row: dict[str, Any]) -> str:
    raw = str(row.get("match_key") or row.get("canonical_match_id") or row.get("event_key") or "")
    date = _date_from_any(row.get("commence_time"), row.get("kickoff"), row.get("start_time"), raw)
    home = row.get("home_team") or row.get("home")
    away = row.get("away_team") or row.get("away")
    parts = _key_parts(raw)
    if parts:
        p_home, p_away, p_date = parts
        home = home or p_home
        away = away or p_away
        date = date or p_date
    if home and away and date:
        return f"{date}|{_norm(home)}|{_norm(away)}"
    return _norm(raw)


def _sig(row: dict[str, Any]) -> str:
    match = _match_key(row)
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
    _write_json(RESCUE, rows)


def _fallback_window(now: datetime) -> tuple[datetime, datetime]:
    min_lead = max(0, _env_int("MIN_KICKOFF_LEAD_MINUTES", 20))
    # Keep a small buffer so a row needed by this run is not lost due to seconds.
    earliest = now + timedelta(minutes=max(0, min_lead - 2))
    hours = max(1, _env_int("PUBLISH_WINDOW_HOURS", _env_int("CONTROLLED_FALLBACK_PUBLISH_WINDOW_HOURS", 2)))
    latest = now + timedelta(hours=hours, minutes=_env_int("RESTORE_AWAITING_MOVEMENT_WINDOW_BUFFER_MINUTES", 10))
    return earliest, latest


def main() -> int:
    now = datetime.now(UTC)
    earliest, latest = _fallback_window(now)
    state = _load_json(STATE, {})
    awaiting = state.get("awaiting_movement_candidates") if isinstance(state, dict) else []
    if not isinstance(awaiting, list):
        awaiting = []
    existing_payload = _load_json(RESCUE, [])
    rescue_rows = _extract_rows(existing_payload)
    # Drop previously restored awaiting rows that have now moved outside the live
    # fallback window; keep non-awaiting rescue rows untouched.
    by_sig = {
        _sig(row): row for row in rescue_rows
        if isinstance(row, dict) and row.get("_candidate_source") != "awaiting_movement_lifecycle"
    }

    restored = 0
    skipped = {"expired": 0, "too_far_24h": 0, "outside_fallback_window": 0, "unknown_kickoff": 0, "bad_row": 0}
    future_watchlist: list[dict[str, Any]] = []
    for row in awaiting:
        if not isinstance(row, dict):
            skipped["bad_row"] += 1
            continue
        kickoff = _parse_dt(row.get("commence_time") or row.get("kickoff") or row.get("start_time"))
        if kickoff is None:
            skipped["unknown_kickoff"] += 1
            continue
        if kickoff < now:
            skipped["expired"] += 1
            continue
        if kickoff > now + timedelta(hours=24):
            skipped["too_far_24h"] += 1
            continue
        if not (earliest <= kickoff <= latest):
            skipped["outside_fallback_window"] += 1
            if len(future_watchlist) < 20:
                future_watchlist.append({
                    "match_key": row.get("match_key") or row.get("canonical_match_id"),
                    "home_team": row.get("home_team") or row.get("home"),
                    "away_team": row.get("away_team") or row.get("away"),
                    "commence_time": kickoff.isoformat(),
                    "minutes_to_kickoff": round((kickoff - now).total_seconds() / 60.0, 2),
                    "selection": row.get("selection"),
                    "point": row.get("point"),
                    "reason": "outside_active_fallback_window",
                })
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
        "window": {"earliest_utc": earliest.isoformat(), "latest_utc": latest.isoformat()},
        "awaiting_seen": len(awaiting),
        "existing_rescue_rows": len(rescue_rows),
        "restored": restored,
        "rescue_rows_after": len(merged),
        "skipped": skipped,
        "future_watchlist_sample": future_watchlist,
        "sample": [x for x in merged if x.get("_candidate_source") == "awaiting_movement_lifecycle"][:10],
    }
    _write_json(OUT, payload)
    print(json.dumps({"status": "ok", "restored": restored, "rescue_rows_after": len(merged), "skipped": skipped}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
