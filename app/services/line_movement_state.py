from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

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
    if not match_key:
        home = _norm(_get(candidate, "home_team"))
        away = _norm(_get(candidate, "away_team"))
        kickoff = _norm(_get(candidate, "commence_time") or _get(candidate, "commence_time_utc"))
        league = _norm(_get(candidate, "league_name"))
        match_key = "|".join(x for x in (league, home, away, kickoff) if x)
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


def _bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on", "force"}


def _next_scheduled_run_at(now: datetime, interval_min: int) -> datetime | None:
    if interval_min <= 0:
        return None
    tz_name = (
        os.getenv("LINE_MOVEMENT_CRON_TIMEZONE")
        or os.getenv("APP_TIMEZONE")
        or os.getenv("TZ")
        or "Europe/Moscow"
    )
    try:
        local_tz = ZoneInfo(tz_name)
    except Exception:
        local_tz = UTC
    now_local = now.astimezone(local_tz)
    anchor_minute = int(float(os.getenv("LINE_MOVEMENT_CRON_ANCHOR_MINUTE") or 0))
    anchor_minute = max(0, min(anchor_minute, 1439))
    day_anchor = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    anchor = day_anchor.replace(hour=anchor_minute // 60, minute=anchor_minute % 60)
    while anchor <= now_local:
        anchor += timedelta(minutes=interval_min)
    return anchor.astimezone(UTC)


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
    current_run_id = os.getenv("GITHUB_RUN_ID") or os.getenv("HARIZON_RUN_ID") or ""
    if current_run_id:
        current["run_id"] = current_run_id

    # A second snapshot must come from a later regular run, not from another
    # script in the same workflow seconds later.  Older snapshots may exist
    # behind a too-fresh snapshot from the current workflow; use the latest
    # eligible previous snapshot instead of blindly using snapshots[-1].
    min_recheck_minutes = float(os.getenv("LINE_MOVEMENT_MIN_RECHECK_MINUTES") or 60.0)

    def _snapshot_age_minutes(snapshot: Any) -> float | None:
        snap_at = _dt(snapshot.get("captured_at_utc")) if isinstance(snapshot, dict) else None
        return ((now - snap_at).total_seconds() / 60.0) if snap_at else None

    previous = None
    too_fresh_previous = snapshots[-1] if snapshots else None
    too_fresh_previous_age = _snapshot_age_minutes(too_fresh_previous)
    for snapshot in reversed(snapshots):
        if not isinstance(snapshot, dict):
            continue
        age = _snapshot_age_minutes(snapshot)
        snap_run_id = str(snapshot.get("run_id") or "")
        same_run = bool(current_run_id and snap_run_id and snap_run_id == current_run_id)
        if no_next_run or (age is not None and age >= min_recheck_minutes and not same_run):
            previous = snapshot
            break

    if previous is None and snapshots:
        previous = None

    current_odds = _float(current.get("odds"), 0.0)
    previous_odds = _float(previous.get("odds") if isinstance(previous, dict) else None, 0.0)
    previous_at = _dt(previous.get("captured_at_utc")) if isinstance(previous, dict) else None
    previous_age_minutes = ((now - previous_at).total_seconds() / 60.0) if previous_at else None
    latest_snapshot_too_fresh = (
        too_fresh_previous is not None
        and too_fresh_previous_age is not None
        and too_fresh_previous_age < min_recheck_minutes
        and previous is None
        and not no_next_run
    )

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
    elif latest_snapshot_too_fresh:
        status = "awaiting_next_run"
        passed = False
        reasons.append(f"needs_later_line_movement_recheck:{too_fresh_previous_age:.1f}<{min_recheck_minutes:.1f}m")
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

    if not latest_snapshot_too_fresh:
        snapshots.append(current)
    entry["snapshots"] = snapshots[-max_snapshots:]
    entry["last_snapshot"] = current
    entry["last_status"] = status
    entry["updated_at_utc"] = now.isoformat()
    lines[key] = entry
    state["updated_at_utc"] = now.isoformat()
    _write(path, state)
    try:
        latest_path = path.parent / "latest.json"
        _write(latest_path, state)
    except Exception:
        latest_path = None

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
        "latest_state_path": str(latest_path) if latest_path else None,
        "previous_snapshot_at_utc": previous.get("captured_at_utc") if isinstance(previous, dict) else None,
        "previous_snapshot_age_minutes": round(previous_age_minutes, 2) if previous_age_minutes is not None else None,
        "min_recheck_minutes": min_recheck_minutes,
        "eligible_previous_snapshot_at_utc": previous.get("captured_at_utc") if isinstance(previous, dict) else None,
        "latest_snapshot_too_fresh_age_minutes": round(too_fresh_previous_age, 2) if too_fresh_previous_age is not None else None,
    }
