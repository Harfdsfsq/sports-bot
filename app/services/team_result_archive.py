"""Retain identified completed SStats results with original receipt timestamps."""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.services.daily_match_registry import parse_time, read_json, write_json
from app.services.fixture_identity import same_history_team_identity


def retain_history(provider, rows, observed_at, *, binding_rows=None):
    now = datetime.now(UTC)
    observed = parse_time(observed_at)
    if observed is not None and observed > now + timedelta(seconds=5):
        observed = None
    if observed is None and not binding_rows:
        return rows, {"retained": 0, "reused": 0}
    path = Path(".data/provider_cache/sstats-team-results-v1.json")
    saved = read_json(path, {"results": {}})
    records = saved.get("results") or {}
    names = {}
    for row in [*rows, *(binding_rows or [])]:
        for side in ("home", "away"):
            team = row.get(side + "Team") or {}
            if isinstance(team, dict) and str(team.get("id") or "").isdecimal() and team.get("name"):
                names.setdefault(str(team["id"]), set()).add(team["name"])
    incoming = {}
    for row in rows if observed is not None else []:
        start = provider._extract_start(row)
        sid = str(row.get("id") or "")
        scores = [provider._extract_result(row, side) for side in ("home", "away")]
        if (
            not sid.isdecimal()
            or int(sid) <= 0
            or not start
            or not now - timedelta(days=365) <= start < now
            or str(row.get("status")) not in {"8", "9", "10", "17", "18"}
            or any(v is None or not math.isfinite(v) or v < 0 or not v.is_integer() for v in scores)
        ):
            continue
        normalized = {
            "id": row["id"],
            "date": start.isoformat(),
            "status": row["status"],
            "homeTeam": row.get("homeTeam"),
            "awayTeam": row.get("awayTeam"),
            "homeFTResult": scores[0],
            "awayFTResult": scores[1],
            "season": {"league": {"name": provider._extract_league_name(row)}},
        }
        if not all(
            isinstance(normalized.get(side + "Team"), dict) and normalized[side + "Team"].get("name")
            for side in ("home", "away")
        ):
            continue
        if sid in incoming and incoming[sid]["row"] != normalized:
            incoming[sid]["conflicting"] = True
        else:
            incoming.setdefault(sid, {"row": normalized, "observed_at": observed.isoformat()})
    for sid, value in incoming.items():
        previous = parse_time((records.get(sid) or {}).get("observed_at"))
        if previous is None or observed > previous:
            records[sid] = value
    records = {
        sid: r
        for sid, r in records.items()
        if parse_time((r.get("row") or {}).get("date"))
        and now - timedelta(days=365) <= parse_time(r["row"]["date"]) < now
    }
    write_json(path, {"results": records})
    current_ids = {str(r.get("id")) for r in rows}
    reused = []
    for sid, receipt in records.items():
        if sid in current_ids or receipt.get("conflicting"):
            continue
        row = receipt["row"]
        if parse_time(row["date"]) < now - timedelta(days=120):
            continue
        bound = False
        compatible = True
        for side in ("home", "away"):
            team = row.get(side + "Team") or {}
            fresh_names = names.get(str(team.get("id")), set())
            if fresh_names:
                agrees = all(same_history_team_identity(team["name"], [name]) for name in fresh_names)
                bound = bound or agrees
                compatible = compatible and agrees
        if bound and compatible:
            reused.append({**row, "_history_observed_at": receipt["observed_at"]})
    return rows + reused, {
        "retained": len(records),
        "reused": len(reused),
        "oldest_reused_observation": min((r["_history_observed_at"] for r in reused), default=None),
    }
