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


def _norm(value: Any) -> str:
    text = str(value or "").strip().casefold()
    text = re.sub(r"[^\w.]+", "_", text, flags=re.UNICODE).strip("_")
    return text


def candidate_lifecycle_key(candidate: Any) -> str:
    match_key = _norm(_get(candidate, "match_key"))
    if not match_key:
        home = _norm(_get(candidate, "home_team"))
        away = _norm(_get(candidate, "away_team"))
        kickoff = _norm(_get(candidate, "commence_time") or _get(candidate, "commence_time_utc"))
        match_key = "|".join(x for x in (home, away, kickoff) if x)
    family = _norm(_get(candidate, "family"))
    selection = _norm(_get(candidate, "selection_key") or _get(candidate, "selection"))
    point = _get(candidate, "point")
    try:
        point_key = f"{float(point):g}" if point not in (None, "") else ""
    except Exception:
        point_key = _norm(point)
    team_side = _norm(_get(candidate, "team_side"))
    return "|".join([match_key, family, selection, point_key, team_side])


def _path() -> Path:
    return Path(os.getenv("CANDIDATE_LIFECYCLE_STATE_PATH") or ".data/candidate-lifecycle-state.json")


def _load(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def _write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def record_candidate_lifecycle(candidate: Any, tier_report: dict[str, Any], reasons: list[str] | None = None, *, now: datetime | None = None) -> dict[str, Any]:
    """Write fresh candidate lifecycle state on every run.

    This file is an operational state index, not a historical artifact.  It is
    updated even for candidates that are not publishable yet, so stale data from
    older runs cannot hide the current A/B tier and line-movement decision.
    """
    now = (now or datetime.now(UTC)).astimezone(UTC)
    path = _path()
    state = _load(path)
    candidates = state.setdefault("candidates", {})
    if not isinstance(candidates, dict):
        candidates = {}
        state["candidates"] = candidates

    line = tier_report.get("line_movement") if isinstance(tier_report, dict) else {}
    key = candidate_lifecycle_key(candidate)
    row = {
        "candidate_key": key,
        "updated_at_utc": now.isoformat(),
        "match_key": str(_get(candidate, "match_key") or ""),
        "home_team": str(_get(candidate, "home_team") or ""),
        "away_team": str(_get(candidate, "away_team") or ""),
        "commence_time": str(_get(candidate, "commence_time") or _get(candidate, "commence_time_utc") or ""),
        "family": str(_get(candidate, "family") or ""),
        "selection": str(_get(candidate, "selection") or _get(candidate, "selection_key") or ""),
        "publication_tier": str(tier_report.get("publication_tier") or ""),
        "can_publish": bool(tier_report.get("can_publish")),
        "found_value": bool(tier_report.get("found_value", True)),
        "found_value_but_blocked": bool(tier_report.get("found_value_but_blocked")),
        "odds_sources_count": int(tier_report.get("odds_sources_count") or 0),
        "context_sources_count": int(tier_report.get("context_sources_count") or 0),
        "line_movement_status": str((line or {}).get("status") or ""),
        "line_movement_passed": bool((line or {}).get("passed")),
        "line_snapshot_count": int((line or {}).get("snapshot_count") or 0),
        "line_state_path": str((line or {}).get("state_path") or ""),
        "reasons": [str(x) for x in (reasons or [])],
    }
    candidates[key] = row

    counts = {
        "total_candidates_seen": len(candidates),
        "tier_a_publishable": sum(1 for item in candidates.values() if item.get("publication_tier") == "A" and item.get("can_publish")),
        "tier_b_publishable": sum(1 for item in candidates.values() if item.get("publication_tier") == "B" and item.get("can_publish")),
        "waiting_line_movement": sum(1 for item in candidates.values() if "wait" in str(item.get("publication_tier") or "") or item.get("line_movement_status") == "awaiting_next_run"),
        "found_value_but_blocked": sum(1 for item in candidates.values() if item.get("found_value_but_blocked")),
    }
    state["updated_at_utc"] = now.isoformat()
    state["counts"] = counts
    _write(path, state)
    return row
