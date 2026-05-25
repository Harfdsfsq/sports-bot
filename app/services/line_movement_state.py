from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

UTC = timezone.utc


def _get(candidate: Any, field: str, default: Any = None) -> Any:
    if isinstance(candidate, dict):
        if field in candidate:
            return candidate.get(field)
        for container_name in ("source_summary", "diagnostics", "analysis"):
            container = candidate.get(container_name)
            if isinstance(container, dict) and field in container:
                return container.get(field)
        return default
    return getattr(candidate, field, default)


def _float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(str(value).replace(",", "."))
    except Exception:
        return default


def _dt(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            text = str(value).strip().replace("Z", "+00:00")
            dt = datetime.fromisoformat(text)
        except Exception:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _norm(value: Any) -> str:
    text = str(value or "").strip().casefold()
    text = re.sub(r"[^\w.]+", "_", text, flags=re.UNICODE).strip("_")
    return text


def _line_key(candidate: Any) -> str:
    match_key = _norm(_get(candidate, "match_key"))
    family = _norm(_get(candidate, "family"))
    selection = _norm(_get(candidate, "selection_key") or _get(candidate, "selection"))
    point = _get(candidate, "point")
    try:
        point_key = f"{float(point):g}" if point not in (None, "") else ""
    except Exception:
        point_key = _norm(point)
    team_side = _norm(_get(candidate, "team_side"))
    return "|".join([match_key, family, selection, point_key, team_side])


def _state_path(candidate: Any, now: datetime) -> Path:
    kickoff = _dt(_get(candidate, "commence_time") or _get(candidate, "commence_time_utc"))
    day = (kickoff or now).date().isoformat()
    return Path(os.getenv("LINE_MOVEMENT_STATE_PATH") or f".data/line_history/{day}.json")


def _load(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def _write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _value_ok(candidate: Any, *, min_ev_pct: float, min_edge_pct: float) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    odds = _float(_get(candidate, "odds") or _get(candidate, "selected_odds") or _get(candidate, "price_used_for_ev"), 0.0)
    ev_pct = _float(_get(candidate, "ev_pct"), 0.0)
    edge_pct = _float(_get(candidate, "edge_pct"), 0.0)
    if odds <= 1.0:
        reasons.append("missing_current_odds")
    if ev_pct < min_ev_pct:
        reasons.append(f"current_ev_below_floor:{ev_pct:.2f}<{min_ev_pct:.2f}")
    if edge_pct < min_edge_pct:
        reasons.append(f"current_edge_below_floor:{edge_pct:.2f}<{min_edge_pct:.2f}")
    return not reasons, reasons


def _snapshot(candidate: Any, now: datetime) -> dict[str, Any]:
    return {
        "captured_at_utc": now.isoformat(),
        "odds": _float(_get(candidate, "odds") or _get(candidate, "selected_odds") or _get(candidate, "price_used_for_ev"), 0.0),
        "ev_pct": _float(_get(candidate, "ev_pct"), 0.0),
        "edge_pct": _float(_get(candidate, "edge_pct"), 0.0),
        "confidence": _float(_get(candidate, "confidence"), 0.0),
        "bookmaker": str(_get(candidate, "bookmaker") or ""),
        "sources_count": int(_float(_get(candidate, "sources_count"), 0.0)),
        "books_count": int(_float(_get(candidate, "books_count"), 0.0)),
    }


def evaluate_and_record_line_movement(candidate: Any, settings: Any, *, now: datetime | None = None) -> dict[str, Any]:
    """Persist one explicit movement snapshot and return a publish lifecycle decision.

    Lifecycle:
    - first snapshot and there is another planned run before kickoff: awaiting_next_run;
    - second+ snapshot: movement_confirmed when odds have not drifted badly and value remains;
    - first snapshot with no realistic next run before kickoff: publish_now_no_next_cron;
    - otherwise: movement_failed / value_failed.
    """
    now = (now or datetime.now(UTC)).astimezone(UTC)
    path = _state_path(candidate, now)
    state = _load(path)
    lines = state.setdefault("lines", {})
    if not isinstance(lines, dict):
        lines = {}
        state["lines"] = lines
    key = _line_key(candidate)
    entry = lines.setdefault(key, {"snapshots": []})
    snapshots = entry.get("snapshots") if isinstance(entry.get("snapshots"), list) else []
    previous = snapshots[-1] if snapshots else None

    next_run_min = int(float(os.getenv("LINE_MOVEMENT_NEXT_RUN_MINUTES") or getattr(settings, "line_movement_next_run_minutes", 120) or 120))
    min_lead_min = int(float(os.getenv("LINE_MOVEMENT_MIN_LEAD_MINUTES") or 15))
    max_adverse_drift_pct = float(os.getenv("LINE_MOVEMENT_MAX_ADVERSE_DRIFT_PCT") or 3.0)
    min_ev_pct = float(os.getenv("LINE_MOVEMENT_MIN_CURRENT_EV_PCT") or 0.0)
    min_edge_pct = float(os.getenv("LINE_MOVEMENT_MIN_CURRENT_EDGE_PCT") or 0.0)
    max_snapshots = int(float(os.getenv("LINE_HISTORY_MAX_SNAPSHOTS_PER_LINE") or 12))

    kickoff = _dt(_get(candidate, "commence_time") or _get(candidate, "commence_time_utc"))
    lead_minutes = ((kickoff - now).total_seconds() / 60.0) if kickoff else None
    no_next_run = lead_minutes is not None and lead_minutes <= next_run_min + min_lead_min
    value_ok, value_reasons = _value_ok(candidate, min_ev_pct=min_ev_pct, min_edge_pct=min_edge_pct)

    current = _snapshot(candidate, now)
    current_odds = _float(current.get("odds"), 0.0)
    previous_odds = _float(previous.get("odds") if isinstance(previous, dict) else None, 0.0)
    line_move_pct = 0.0
    adverse_drift = False
    if previous_odds > 1.0 and current_odds > 1.0:
        # For a value pick, shortening is positive market confirmation; large drift upward is adverse.
        line_move_pct = (current_odds - previous_odds) / previous_odds * 100.0
        adverse_drift = line_move_pct > abs(max_adverse_drift_pct)

    reasons: list[str] = []
    if not value_ok:
        reasons.extend(value_reasons)
    if previous is None and not no_next_run:
        status = "awaiting_next_run"
        passed = False
        reasons.append("needs_next_cron_line_movement_recheck")
    elif previous is None and no_next_run:
        status = "publish_now_no_next_cron" if value_ok else "value_failed"
        passed = value_ok
    elif adverse_drift:
        status = "movement_failed"
        passed = False
        reasons.append(f"line_drifted_against_candidate:{line_move_pct:.2f}%>{max_adverse_drift_pct:.2f}%")
    elif value_ok:
        status = "movement_confirmed"
        passed = True
    else:
        status = "value_failed"
        passed = False

    snapshots.append(current)
    entry["snapshots"] = snapshots[-max_snapshots:]
    entry["last_snapshot"] = current
    entry["last_status"] = status
    entry["updated_at_utc"] = now.isoformat()
    lines[key] = entry
    state["updated_at_utc"] = now.isoformat()
    _write(path, state)

    return {
        "passed": passed,
        "status": status,
        "reasons": reasons,
        "line_key": key,
        "snapshot_count": len(snapshots),
        "current_odds": current_odds or None,
        "previous_odds": previous_odds or None,
        "line_move_pct": round(line_move_pct, 4),
        "lead_minutes": round(lead_minutes, 2) if lead_minutes is not None else None,
        "no_more_cron_before_kickoff": no_next_run,
        "state_path": str(path),
        "previous_snapshot_at_utc": previous.get("captured_at_utc") if isinstance(previous, dict) else None,
    }
