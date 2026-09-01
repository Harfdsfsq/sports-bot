from __future__ import annotations

"""Install the required two-hour line-movement gate before Telegram publication.

The bot is run externally by cron.  This module does not schedule anything; it
only makes publication respect the rule: a value found before the next regular
run is stored first, refreshed on the next run, and published only if value and
line movement remain acceptable.
"""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


def _get(candidate: Any, field: str) -> Any:
    if isinstance(candidate, dict):
        if field in candidate:
            return candidate.get(field)
        for container_name in ("source_summary", "diagnostics", "publication_lifecycle"):
            container = candidate.get(container_name)
            if isinstance(container, dict) and field in container:
                return container.get(field)
        return None
    if hasattr(candidate, field):
        return getattr(candidate, field)
    for container_name in ("source_summary", "diagnostics"):
        container = getattr(candidate, container_name, None)
        if isinstance(container, dict) and field in container:
            return container.get(field)
    return None


def _parse_dt(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
        except Exception:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _candidate_price(candidate: Any) -> float | None:
    for field in ("selected_odds", "odds", "price_used_for_ev"):
        value = _get(candidate, field)
        try:
            if value in (None, ""):
                continue
            parsed = float(str(value).replace(",", "."))
            if parsed > 1.0:
                return parsed
        except Exception:
            continue
    return None


def _candidate_point(candidate: Any) -> float | None:
    value = _get(candidate, "point")
    try:
        if value in (None, ""):
            return None
        return float(str(value).replace(",", "."))
    except Exception:
        return None


def _selection_key(candidate: Any) -> str:
    raw = str(_get(candidate, "selection_key") or _get(candidate, "selection") or "").strip().lower()
    if raw in {"больше", "over", "o", "tb", "тб"}:
        return "over"
    if raw in {"меньше", "under", "u", "tm", "тм"}:
        return "under"
    return raw


def _value_still_positive(candidate: Any) -> bool:
    for field in ("ev_pct", "edge_pct"):
        try:
            value = _get(candidate, field)
            if value in (None, ""):
                continue
            if float(str(value).replace(",", ".")) > 0:
                return True
        except Exception:
            continue
    try:
        prob = float(str(_get(candidate, "final_probability") or _get(candidate, "adjusted_probability") or "0").replace(",", "."))
        price = _candidate_price(candidate) or 0.0
        return prob > 0 and price > 1.0 and prob * price > 1.0
    except Exception:
        return False


def _is_point_move_adverse(candidate: Any, first_point: float | None, current_point: float | None) -> bool:
    if first_point is None or current_point is None or abs(first_point - current_point) < 1e-9:
        return False
    family = str(_get(candidate, "family") or "").strip().lower()
    selection = _selection_key(candidate)
    if family == "totals":
        return (selection == "over" and current_point > first_point) or (selection == "under" and current_point < first_point)
    return False


def _load_state(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def _write_state(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _candidate_key(candidate: Any) -> str:
    try:
        from app.services.publication_lifecycle import candidate_dedupe_keys
        keys = sorted(candidate_dedupe_keys(candidate))
        if keys:
            return keys[0]
    except Exception:
        pass
    raw = _get(candidate, "fingerprint") or _get(candidate, "prediction_id") or _get(candidate, "id")
    if raw:
        return "exact:" + str(raw).strip().lower()
    return ""


def _evaluate(candidate: Any, settings: Any) -> tuple[bool, str, dict[str, Any]]:
    enabled = str(getattr(settings, "candidate_lifecycle_gate_enabled", "true")).strip().lower() not in {"0", "false", "no", "off"}
    now = datetime.now(timezone.utc)
    interval_hours = max(1.0, float(getattr(settings, "regular_run_interval_hours", 2.0) or 2.0))
    tolerance_pct = max(0.0, float(getattr(settings, "candidate_lifecycle_price_drift_tolerance_pct", 1.0) or 1.0))
    state_path = Path(str(getattr(settings, "candidate_lifecycle_state_path", ".data/candidate-lifecycle-state.json") or ".data/candidate-lifecycle-state.json"))
    key = _candidate_key(candidate)
    kickoff = _parse_dt(_get(candidate, "commence_time") or _get(candidate, "commence_time_utc") or _get(candidate, "start_time"))
    current_price = _candidate_price(candidate)
    current_point = _candidate_point(candidate)
    report: dict[str, Any] = {
        "enabled": enabled,
        "candidate_key": key,
        "state_path": str(state_path),
        "now_utc": now.isoformat(),
        "run_interval_hours": interval_hours,
        "current_price": current_price,
        "current_point": current_point,
    }
    if not enabled:
        report["stage"] = "disabled"
        return True, "disabled", report
    if not key:
        report["stage"] = "missing_candidate_key"
        return False, "missing_candidate_key", report
    if kickoff is None:
        report["stage"] = "missing_kickoff"
        return False, "missing_kickoff", report
    next_run = now + timedelta(hours=interval_hours)
    immediate = kickoff <= next_run
    value_positive = _value_still_positive(candidate)
    report.update({
        "kickoff_utc": kickoff.isoformat(),
        "next_regular_run_utc": next_run.isoformat(),
        "immediate_before_next_run": immediate,
        "value_still_positive": value_positive,
        "price_drift_tolerance_pct": tolerance_pct,
    })

    state = _load_state(state_path)
    candidates = state.get("candidates") if isinstance(state.get("candidates"), dict) else {}
    existing = candidates.get(key) if isinstance(candidates.get(key), dict) else None
    if existing is None:
        candidates[key] = {
            "first_seen_at": now.isoformat(),
            "last_seen_at": now.isoformat(),
            "status": "ready_final_check" if immediate else "awaiting_movement_check",
            "first_price": current_price,
            "first_point": current_point,
            "last_price": current_price,
            "last_point": current_point,
            "kickoff_utc": kickoff.isoformat(),
            "match_key": str(_get(candidate, "match_key") or ""),
            "family": str(_get(candidate, "family") or ""),
            "selection": str(_get(candidate, "selection") or _get(candidate, "selection_key") or ""),
        }
        state["candidates"] = candidates
        state["updated_at"] = now.isoformat()
        _write_state(state_path, state)
        report["stage"] = candidates[key]["status"]
        report["first_seen"] = True
        if immediate:
            return True, "final_check_before_next_run", report
        return False, "awaiting_next_run_movement_check", report

    first_seen = _parse_dt(existing.get("first_seen_at"))
    try:
        first_price = float(existing.get("first_price")) if existing.get("first_price") not in (None, "") else None
    except Exception:
        first_price = None
    try:
        first_point = float(existing.get("first_point")) if existing.get("first_point") not in (None, "") else None
    except Exception:
        first_point = None
    seen_in_previous_run = bool(first_seen and now.replace(second=0, microsecond=0) > first_seen.replace(second=0, microsecond=0))
    adverse_price = bool(first_price is not None and current_price is not None and current_price > first_price * (1.0 + tolerance_pct / 100.0))
    adverse_point = _is_point_move_adverse(candidate, first_point, current_point)
    report.update({
        "first_seen_at": existing.get("first_seen_at"),
        "previous_status": existing.get("status"),
        "first_price": first_price,
        "first_point": first_point,
        "seen_in_previous_run": seen_in_previous_run,
        "adverse_price_drift": adverse_price,
        "adverse_point_move": adverse_point,
    })
    existing.update({
        "last_seen_at": now.isoformat(),
        "last_price": current_price,
        "last_point": current_point,
        "last_value_positive": value_positive,
        "last_adverse_price_drift": adverse_price,
        "last_adverse_point_move": adverse_point,
    })
    if not value_positive:
        existing["status"] = "rejected_value_gone"
        reason = "value_gone_after_refresh"
    elif adverse_price or adverse_point:
        existing["status"] = "rejected_line_moved_against"
        reason = "line_moved_against_candidate"
    elif not immediate and not seen_in_previous_run:
        existing["status"] = "awaiting_movement_check"
        reason = "awaiting_next_run_movement_check"
    else:
        existing["status"] = "ready_final_check"
        reason = "movement_neutral_or_positive"
    state["updated_at"] = now.isoformat()
    _write_state(state_path, state)
    report["stage"] = existing["status"]
    return reason == "movement_neutral_or_positive" or reason == "final_check_before_next_run", reason, report


def install() -> dict[str, Any]:
    try:
        from app.services.runner import PredictionRunner
    except Exception as exc:
        return {"installed": False, "error": f"import_failed:{type(exc).__name__}: {exc}"}
    if getattr(PredictionRunner, "_rules_lifecycle_gate_installed", False):
        return {"installed": True, "already_installed": True}
    original = PredictionRunner._filter_publishable_candidates

    def _filter_publishable_candidates_with_lifecycle(self: Any, candidates: list[Any]) -> list[Any]:
        base_publishable = original(self, candidates)
        gated: list[Any] = []
        for candidate in base_publishable:
            passed, reason, report = _evaluate(candidate, self.settings)
            try:
                candidate.diagnostics.setdefault("publication_lifecycle_gate", report)
            except Exception:
                pass
            try:
                candidate.source_summary["publication_lifecycle_gate"] = report
                candidate.source_summary["publication_lifecycle_stage"] = report.get("stage") or reason
            except Exception:
                pass
            if passed:
                gated.append(candidate)
                continue
            try:
                candidate.source_summary["publication_blocked_reason"] = reason
            except Exception:
                pass
            try:
                candidate.reasons.append(f"publication_lifecycle={reason}")
            except Exception:
                pass
        return gated

    PredictionRunner._filter_publishable_candidates = _filter_publishable_candidates_with_lifecycle
    PredictionRunner._rules_lifecycle_gate_installed = True
    return {
        "installed": True,
        "patch": "PredictionRunner._filter_publishable_candidates",
        "gate": "two_hour_line_movement_lifecycle",
    }
