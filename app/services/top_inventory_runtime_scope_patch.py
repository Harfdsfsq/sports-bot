from __future__ import annotations

"""Restrict run-once processing to the current top day-inventory scope.

The daily inventory is deliberately capped at 300 top matches.  Some runtime
layers can still re-introduce broad provider rows after the first bootstrap
filter (for example when day inventory rows are merged back with slightly
different keys, or when provider-target wrappers receive an expanded match
list).  This patch therefore scopes three boundaries:

* PredictionRunner._fetch_matches output;
* PredictionRunner._merge_day_inventory_matches output;
* PredictionRunner._filter_matches output and provider target inputs.

The allowlist is intentionally strict: exact inventory match keys plus ordered
home/away/date identities.  It no longer adds reversed/sorted key variants,
because those variants were too permissive and let same-day non-top rows leak
into progressive state.
"""

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

UTC = timezone.utc
ROOT = Path(__file__).resolve().parents[2]
EXPORT_DIR = ROOT / ".data" / "exports"
DAY_INV_DIR = ROOT / ".data" / "day_inventory"
REPORT_PATH = EXPORT_DIR / "latest-top-inventory-runtime-scope.json"
PROGRESSIVE_STATE_PATH = DAY_INV_DIR / "progressive_coverage_state.json"
PROGRESSIVE_EXPORT_PATH = EXPORT_DIR / "latest-progressive-coverage-state.json"
_MARKER = "_harizon_top_inventory_runtime_scope_v5_rolling_topup"


def _truthy(value: Any, default: bool = False) -> bool:
    if value is None or str(value).strip() == "":
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on", "force"}


def _to_int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ""):
            return default
        return int(float(str(value)))
    except Exception:
        return default


def _write_json(path: Path, payload: Any) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except Exception:
        pass


def _read_json(path: Path, default: Any = None) -> Any:
    try:
        if path.exists() and path.stat().st_size > 0:
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default
    return default


def _append_report(stage: str, payload: dict[str, Any]) -> None:
    current = _read_json(REPORT_PATH, {})
    if not isinstance(current, dict):
        current = {}
    events = current.get("events") if isinstance(current.get("events"), list) else []
    events.append({"stage": stage, **payload})
    merged = {**current, **payload, "stage": stage, "events": events[-20:]}
    _write_json(REPORT_PATH, merged)


def _app_tz() -> ZoneInfo | timezone:
    name = str(os.getenv("APP_TIMEZONE") or os.getenv("TZ") or "Europe/Moscow").strip()
    try:
        return ZoneInfo(name)
    except Exception:
        return UTC


def _target_date() -> str:
    explicit = str(os.getenv("DAY_INVENTORY_TARGET_DATE") or os.getenv("DAY_INVENTORY_CACHE_DATE") or "").strip()
    if explicit:
        return explicit
    payload = _read_json(DAY_INV_DIR / "latest.json", {})
    if isinstance(payload, dict) and payload.get("date_local"):
        return str(payload.get("date_local"))
    return datetime.now(UTC).astimezone(_app_tz()).date().isoformat()



def _frozen_roster_path(date: str) -> Path:
    return DAY_INV_DIR / f"frozen_inventory_roster_{date}.json"


def _runtime_topup_roster_path(date: str) -> Path:
    return DAY_INV_DIR / f"runtime_topup_roster_{date}.json"


def _min_valid_roster_rows(max_matches: int) -> int:
    # A frozen roster with just the last few not-started matches is worse than no
    # roster: it changes the day denominator from 300 to 3 and makes coverage look
    # perfect while the real inventory is hidden.  Accept tiny rosters only when no
    # fuller same-day inventory is available.
    configured = _to_int(os.getenv("TOP_INVENTORY_FROZEN_MIN_VALID_ROWS") or os.getenv("DAY_INVENTORY_FROZEN_MIN_VALID_ROWS"), 0)
    if configured > 0:
        return min(max_matches, configured)
    return min(max_matches, max(50, int(max_matches * 0.5)))


def _candidate_inventory_paths(date: str) -> list[Path]:
    return [DAY_INV_DIR / f"{date}.json", DAY_INV_DIR / "current.json", DAY_INV_DIR / "latest.json", DAY_INV_DIR / "today.json"]


def _rows_from_inventory_path(path: Path, date: str, max_matches: int, *, allow_cross_date: bool = False) -> list[dict[str, Any]]:
    payload = _read_json(path, {})
    if not isinstance(payload, dict) or not isinstance(payload.get("matches"), list):
        return []
    if allow_cross_date:
        rows = [r for r in payload.get("matches", []) if isinstance(r, dict)]
    else:
        rows = [r for r in payload.get("matches", []) if isinstance(r, dict) and (_row_date(r) in {"", date})]
    return rows[:max_matches]


def _best_source_inventory(date: str, max_matches: int) -> tuple[Path | None, list[dict[str, Any]]]:
    best_path: Path | None = None
    best_rows: list[dict[str, Any]] = []
    for path in _candidate_inventory_paths(date):
        rows = _rows_from_inventory_path(path, date, max_matches)
        if len(rows) > len(best_rows):
            best_path = path
            best_rows = rows
        if len(best_rows) >= max_matches:
            break
    return best_path, best_rows


def _write_frozen_roster(date: str, max_matches: int, rows: list[dict[str, Any]], source_path: Path | None, *, reason: str) -> dict[str, Any]:
    frozen = _frozen_roster_path(date)
    now = datetime.now(UTC).isoformat()
    frozen_payload = {
        "version": "frozen_day_inventory_roster_v2_min_valid",
        "date_local": date,
        "created_at_utc": now,
        "updated_at_utc": now,
        "source_path": str(source_path) if source_path else None,
        "target_size": max_matches,
        "matches": rows[:max_matches],
        "repair_reason": reason,
        "notes": [
            "Created/repaired before the run starts by top_inventory_runtime_scope_patch.",
            "Tiny frozen rosters are ignored when a fuller same-day day-inventory exists, so the denominator cannot collapse from top-300 to the last few future matches.",
        ],
    }
    _write_json(frozen, frozen_payload)
    try:
        _write_json(EXPORT_DIR / "latest-day-inventory-frozen-roster.json", {k: v for k, v in frozen_payload.items() if k != "matches"} | {"frozen_rows": len(frozen_payload["matches"])})
    except Exception:
        pass
    return {"enabled": True, "path": str(frozen), "created": True, "rows": len(frozen_payload["matches"]), "source_path": str(source_path) if source_path else None, "reason": reason}


def _ensure_frozen_roster(date: str, max_matches: int) -> dict[str, Any]:
    if not _truthy(os.getenv("DAY_INVENTORY_FREEZE_ROSTER_ENABLED", "true"), True):
        return {"enabled": False, "reason": "disabled"}
    frozen = _frozen_roster_path(date)
    min_valid = _min_valid_roster_rows(max_matches)
    source_path, source_rows = _best_source_inventory(date, max_matches)
    existing = _read_json(frozen, {})
    existing_rows = []
    if isinstance(existing, dict) and str(existing.get("date_local") or "") == date and isinstance(existing.get("matches"), list):
        existing_rows = [r for r in existing.get("matches", []) if isinstance(r, dict) and (_row_date(r) in {"", date})]

    if existing_rows:
        # Keep a normal frozen roster.  Repair only pathological tiny rosters when
        # the authoritative day inventory still has a much fuller top list.
        if len(existing_rows) >= min_valid or len(source_rows) <= len(existing_rows):
            return {
                "enabled": True,
                "path": str(frozen),
                "exists": True,
                "rows": len(existing_rows),
                "min_valid_rows": min_valid,
                "source_rows": len(source_rows),
                "valid": len(existing_rows) >= min_valid,
            }
        return _write_frozen_roster(date, max_matches, source_rows, source_path, reason=f"repair_tiny_existing_roster:{len(existing_rows)}<{min_valid};source_rows={len(source_rows)}")

    if source_rows:
        return _write_frozen_roster(date, max_matches, source_rows, source_path, reason="create_from_full_day_inventory")
    return {"enabled": True, "path": str(frozen), "created": False, "rows": 0, "min_valid_rows": min_valid, "source_rows": 0, "reason": "no_inventory_rows"}


def _runtime_window_hours() -> float:
    try:
        return max(1.0, float(os.getenv("TOP_INVENTORY_RUNTIME_TOPUP_WINDOW_HOURS") or os.getenv("PUBLISH_WINDOW_HOURS") or 24))
    except Exception:
        return 24.0


def _runtime_min_future_rows() -> int:
    return max(1, _to_int(os.getenv("TOP_INVENTORY_RUNTIME_MIN_FUTURE_ROWS") or 10, 10))


def _coerce_utc(value: Any) -> datetime | None:
    return _parse_dt(value)


def _match_dt(match: Any) -> datetime | None:
    if isinstance(match, dict):
        return _coerce_utc(match.get("commence_time") or match.get("kickoff_utc") or match.get("start_time") or match.get("kickoff") or match.get("kickoff_local"))
    return _coerce_utc(getattr(match, "commence_time", None))


def _is_future_match(match: Any, now_utc: Any, *, window_hours: float | None = None, min_lead_minutes: float = 0.0) -> bool:
    dt = _match_dt(match)
    now = _coerce_utc(now_utc) or datetime.now(UTC)
    if dt is None:
        return False
    lead = (dt - now).total_seconds() / 60.0
    if lead < min_lead_minutes:
        return False
    if window_hours is not None and lead > float(window_hours) * 60.0:
        return False
    return True


def _future_count(matches: list[Any], now_utc: Any, *, window_hours: float | None = None, min_lead_minutes: float = 0.0) -> int:
    return sum(1 for match in matches if _is_future_match(match, now_utc, window_hours=window_hours, min_lead_minutes=min_lead_minutes))


def _future_matches(matches: list[Any], now_utc: Any, *, window_hours: float | None = None, min_lead_minutes: float = 0.0) -> list[Any]:
    out = [m for m in matches if _is_future_match(m, now_utc, window_hours=window_hours, min_lead_minutes=min_lead_minutes)]
    out.sort(key=lambda m: (_match_dt(m) or datetime.max.replace(tzinfo=UTC), _match_home(m), _match_away(m)))
    return out


def _row_from_match(match: Any) -> dict[str, Any]:
    if isinstance(match, dict):
        row = dict(match)
        if not row.get("kickoff_utc") and row.get("commence_time"):
            row["kickoff_utc"] = row.get("commence_time")
        return row
    dt = _match_dt(match)
    meta = getattr(match, "metadata", None) if isinstance(getattr(match, "metadata", None), dict) else {}
    return {
        "source": getattr(match, "source", "runtime_topup"),
        "source_event_id": getattr(match, "source_event_id", ""),
        "sport_key": getattr(match, "sport_key", "soccer"),
        "league_name": getattr(match, "league_name", ""),
        "home_team": getattr(match, "home_team", ""),
        "away_team": getattr(match, "away_team", ""),
        "kickoff_utc": dt.isoformat() if dt is not None else "",
        "match_key": _match_key(match),
        "metadata": {**meta, "runtime_topup_roster": True},
    }


def _write_runtime_topup_roster(date: str, max_matches: int, matches: list[Any], now_utc: Any, *, reason: str) -> dict[str, Any]:
    if not _truthy(os.getenv("TOP_INVENTORY_RUNTIME_TOPUP_ENABLED", "true"), True):
        return {"enabled": False, "reason": "disabled"}
    window_hours = _runtime_window_hours()
    future = _future_matches(matches, now_utc, window_hours=window_hours, min_lead_minutes=0)[:max_matches]
    if not future:
        return {"enabled": True, "created": False, "reason": "no_future_matches", "rows": 0, "window_hours": window_hours}
    path = _runtime_topup_roster_path(date)
    rows = [_row_from_match(match) for match in future]
    now = datetime.now(UTC).isoformat()
    payload = {
        "version": "runtime_topup_roster_v1",
        "date_local": date,
        "created_at_utc": now,
        "updated_at_utc": now,
        "target_size": max_matches,
        "window_hours": window_hours,
        "reason": reason,
        "matches": rows,
        "notes": [
            "Runtime-only rolling roster used when the frozen calendar-day top-300 has no future rows left.",
            "This file may include next-local-date matches so the 2-hour runner can keep processing the next 12-24h window.",
        ],
    }
    _write_json(path, payload)
    _write_json(EXPORT_DIR / "latest-top-inventory-runtime-topup.json", {k: v for k, v in payload.items() if k != "matches"} | {"rows": len(rows)})
    return {"enabled": True, "created": True, "path": str(path), "rows": len(rows), "window_hours": window_hours, "reason": reason}


def _valid_runtime_topup_rows(date: str, max_matches: int) -> tuple[Path | None, list[dict[str, Any]], dict[str, Any]]:
    path = _runtime_topup_roster_path(date)
    rows = _rows_from_inventory_path(path, date, max_matches, allow_cross_date=True)
    now = datetime.now(UTC)
    future = _future_matches(rows, now, window_hours=_runtime_window_hours(), min_lead_minutes=0)
    min_future = _runtime_min_future_rows()
    report = {"enabled": True, "path": str(path), "rows": len(rows), "future_rows": len(future), "min_future_rows": min_future}
    if len(future) >= min_future:
        return path, future[:max_matches], {**report, "valid": True}
    return None, [], {**report, "valid": False}

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
    text = str(value or "").lower().strip()
    text = re.sub(r"[^a-z0-9а-яё]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _compact(value: Any) -> str:
    return _norm(value).replace(" ", "_")


def _local_date_from_any(value: Any) -> str:
    dt = _parse_dt(value)
    if dt is None:
        return ""
    try:
        return dt.astimezone(_app_tz()).date().isoformat()
    except Exception:
        return dt.date().isoformat()


def _row_date(row: dict[str, Any]) -> str:
    for key in ("date_local", "kickoff_utc", "commence_time", "start_time", "kickoff"):
        value = row.get(key)
        if not value:
            continue
        if key == "date_local" and re.match(r"^20\d\d-\d\d-\d\d$", str(value)):
            return str(value)
        date = _local_date_from_any(value)
        if date:
            return date
    key = str(row.get("match_key") or row.get("canonical_match_id") or "")
    match = re.search(r"(20\d\d-\d\d-\d\d)", key)
    return match.group(1) if match else ""


def _match_key(match: Any) -> str:
    if isinstance(match, dict):
        return str(match.get("match_key") or match.get("canonical_match_id") or "").strip()
    return str(getattr(match, "match_key", "") or "").strip()


def _match_home(match: Any) -> str:
    if isinstance(match, dict):
        return str(match.get("home_team") or match.get("home") or "")
    return str(getattr(match, "home_team", "") or "")


def _match_away(match: Any) -> str:
    if isinstance(match, dict):
        return str(match.get("away_team") or match.get("away") or "")
    return str(getattr(match, "away_team", "") or "")


def _match_date(match: Any) -> str:
    if isinstance(match, dict):
        return _row_date(match)
    return _local_date_from_any(getattr(match, "commence_time", None))


def _identity(home: Any, away: Any, date: str) -> str:
    home_c = _compact(home)
    away_c = _compact(away)
    if not home_c or not away_c or not date:
        return ""
    # Ordered home/away identity only.  Do not add reversed/sorted variants: those
    # caused non-top rows to pass scope after midnight.
    return f"soccer|{home_c}|{away_c}|{date}"


def _match_identity(match: Any) -> str:
    return _identity(_match_home(match), _match_away(match), _match_date(match))


def _is_day_inventory_match(match: Any) -> bool:
    if isinstance(match, dict):
        meta = match.get("metadata") if isinstance(match.get("metadata"), dict) else {}
        return bool(match.get("source") == "day_inventory" or meta.get("day_inventory"))
    meta = getattr(match, "metadata", None)
    return bool(getattr(match, "source", "") == "day_inventory" or (isinstance(meta, dict) and meta.get("day_inventory")))


def _inventory_scope() -> tuple[dict[str, set[str]], dict[str, Any]]:
    date = _target_date()
    max_matches = max(1, _to_int(os.getenv("TOP_INVENTORY_RUNTIME_MAX_MATCHES") or os.getenv("DAY_INVENTORY_MAX_MATCHES") or os.getenv("DAY_INVENTORY_TARGET_SIZE") or 300, 300))
    freeze_report = _ensure_frozen_roster(date, max_matches)
    topup_path, topup_rows, topup_report = _valid_runtime_topup_rows(date, max_matches)
    candidate_paths: list[tuple[Path, bool, dict[str, Any]]] = []
    if topup_path is not None and topup_rows:
        candidate_paths.append((topup_path, True, {"runtime_topup": topup_report}))
    candidate_paths.extend([
        (_frozen_roster_path(date), False, {}),
        (DAY_INV_DIR / f"{date}.json", False, {}),
        (DAY_INV_DIR / "current.json", False, {}),
        (DAY_INV_DIR / "latest.json", False, {}),
    ])
    for path, allow_cross_date, path_extra in candidate_paths:
        rows = _rows_from_inventory_path(path, date, max_matches, allow_cross_date=allow_cross_date)
        if not rows:
            continue
        direct_keys: set[str] = set()
        identities: set[str] = set()
        for row in rows:
            direct = str(row.get("match_key") or row.get("canonical_match_id") or "").strip()
            if direct:
                direct_keys.add(direct)
            ident = _identity(row.get("home_team") or row.get("home"), row.get("away_team") or row.get("away"), _row_date(row) or date)
            if ident:
                identities.add(ident)
        scope = {"direct_keys": direct_keys, "identities": identities}
        info = {
            "date_local": date,
            "inventory_path": str(path),
            "inventory_rows": len(rows),
            "direct_keys": len(direct_keys),
            "identity_keys": len(identities),
            "allowlist_keys": len(direct_keys) + len(identities),
            "max_matches": max_matches,
            "frozen_roster": freeze_report,
            **path_extra,
        }
        return scope, info
    return {"direct_keys": set(), "identities": set()}, {"date_local": date, "inventory_path": None, "inventory_rows": 0, "direct_keys": 0, "identity_keys": 0, "allowlist_keys": 0, "max_matches": max_matches}


def _in_scope(match: Any, scope: dict[str, set[str]]) -> bool:
    direct = _match_key(match)
    if direct and direct in scope.get("direct_keys", set()):
        return True
    ident = _match_identity(match)
    return bool(ident and ident in scope.get("identities", set()))


def _dedupe_scoped(matches: list[Any], max_matches: int) -> list[Any]:
    chosen: dict[str, Any] = {}
    order: list[str] = []
    for match in matches:
        ident = _match_identity(match) or _match_key(match)
        if not ident:
            continue
        old = chosen.get(ident)
        if old is None:
            chosen[ident] = match
            order.append(ident)
            continue
        # Prefer the canonical day-inventory row when duplicate provider rows use a
        # different match_key for the same fixture.
        if _is_day_inventory_match(match) and not _is_day_inventory_match(old):
            chosen[ident] = match
    out = [chosen[key] for key in order if key in chosen]
    return out[:max_matches] if max_matches > 0 else out


def _filter_matches(matches: list[Any], scope: dict[str, set[str]], max_matches: int) -> list[Any]:
    if not scope.get("direct_keys") and not scope.get("identities"):
        return matches[:max_matches] if max_matches > 0 else matches
    return _dedupe_scoped([m for m in matches if _in_scope(m, scope)], max_matches)



def _prune_progressive_state_to_scope(scope: dict[str, set[str]], info: dict[str, Any], stage: str) -> dict[str, Any]:
    """Remove same-day non-top rows left by previous broad runs.

    The progressive coverage patch only date-prunes its state.  After a broad
    provider expansion, same-date rows outside top-300 can stay in
    progressive_coverage_state and make reports show 300+ active matches even
    when the current runtime match list is scoped.  Prune those rows using the
    same strict direct-key/ordered-identity scope.
    """
    payload = _read_json(PROGRESSIVE_STATE_PATH, {})
    if not isinstance(payload, dict):
        return {"progressive_pruned": 0, "progressive_kept": 0}
    rows = payload.get("matches") if isinstance(payload.get("matches"), dict) else {}
    if not rows:
        return {"progressive_pruned": 0, "progressive_kept": 0}
    kept: dict[str, Any] = {}
    pruned = 0
    for key, row in rows.items():
        if not isinstance(row, dict):
            pruned += 1
            continue
        row_with_key = {**row, "match_key": row.get("match_key") or key}
        direct = str(row_with_key.get("match_key") or key or "").strip()
        ident = _identity(row_with_key.get("home_team"), row_with_key.get("away_team"), _row_date(row_with_key) or str(info.get("date_local") or ""))
        if (direct and direct in scope.get("direct_keys", set())) or (ident and ident in scope.get("identities", set())):
            kept[str(key)] = row
        else:
            pruned += 1
    if pruned:
        payload["matches"] = kept
        payload["top_inventory_scope_pruned_at_utc"] = datetime.now(UTC).isoformat()
        payload["top_inventory_scope_pruned_stage"] = stage
        payload["top_inventory_scope_pruned_rows"] = int(payload.get("top_inventory_scope_pruned_rows") or 0) + pruned
        _write_json(PROGRESSIVE_STATE_PATH, payload)
        _write_json(PROGRESSIVE_EXPORT_PATH, payload)
    return {"progressive_pruned": pruned, "progressive_kept": len(kept)}


def _scope_result(stage: str, matches: list[Any], *, extra: dict[str, Any] | None = None, now_utc: Any | None = None) -> tuple[list[Any], dict[str, Any]]:
    now_value = _coerce_utc(now_utc) or datetime.now(UTC)
    scope, info = _inventory_scope()
    max_matches = int(info.get("max_matches") or 300)
    filtered = _filter_matches(matches, scope, max_matches)
    fail_open_min = max(1, _to_int(os.getenv("TOP_INVENTORY_RUNTIME_FAIL_OPEN_MIN_MATCHES") or 20, 20))
    min_future = _runtime_min_future_rows()
    topup_report: dict[str, Any] = {}
    raw_future = _future_count(matches, now_value, window_hours=_runtime_window_hours())
    filtered_future = _future_count(filtered, now_value, window_hours=_runtime_window_hours())
    if stage in {"fetch_matches", "merge_day_inventory"} and raw_future >= min_future and filtered_future < min_future:
        topup_report = _write_runtime_topup_roster(str(info.get("date_local") or _target_date()), max_matches, matches, now_value, reason=f"rolling_topup_from_{stage}:filtered_future={filtered_future};raw_future={raw_future}")
        if topup_report.get("created"):
            scope, info = _inventory_scope()
            max_matches = int(info.get("max_matches") or 300)
            filtered = _filter_matches(matches, scope, max_matches)
            filtered_future = _future_count(filtered, now_value, window_hours=_runtime_window_hours())
    used_fail_open = bool((scope.get("direct_keys") or scope.get("identities")) and matches and len(filtered) < fail_open_min and filtered_future < min_future)
    final_matches = matches[:max_matches] if used_fail_open and max_matches > 0 else (matches if used_fail_open else filtered)
    prune_report = _prune_progressive_state_to_scope(scope, info, stage)
    report = {
        **info,
        "enabled": True,
        "input_matches": len(matches),
        "output_matches": len(final_matches),
        "filtered_out": max(0, len(matches) - len(final_matches)),
        "fail_open": used_fail_open,
        "fail_open_min_matches": fail_open_min,
        "raw_future_matches": raw_future,
        "filtered_future_matches": filtered_future,
        "runtime_topup_report": topup_report,
        **prune_report,
    }
    if extra:
        report.update(extra)
    _append_report(stage, report)
    return final_matches, report




def _direct_day_inventory_matches(self: Any, now_utc: Any) -> tuple[list[Any], dict[str, Any]]:
    """Load canonical Match objects directly from the current top-300 inventory file.

    Do not rely on PredictionRunner._load_day_inventory_matches here: that helper
    can read an intermediate broad inventory during intraday rebuilds and may load
    400 provider rows.  This direct reader uses the final top inventory files and
    hard-caps to TOP_INVENTORY_RUNTIME_MAX_MATCHES/DAY_INVENTORY_MAX_MATCHES.
    """
    try:
        from app.schemas import Match
        from app.utils import canonicalize_league_name, canonicalize_team_name, parse_datetime
    except Exception as exc:
        return [], {"enabled": False, "reason": f"import_error:{type(exc).__name__}: {exc}"}

    date = _target_date()
    max_matches = max(1, _to_int(os.getenv("TOP_INVENTORY_RUNTIME_MAX_MATCHES") or os.getenv("DAY_INVENTORY_MAX_MATCHES") or os.getenv("DAY_INVENTORY_TARGET_SIZE") or 300, 300))
    freeze_report = _ensure_frozen_roster(date, max_matches)
    topup_path, topup_rows, topup_report = _valid_runtime_topup_rows(date, max_matches)
    paths: list[tuple[Path, bool]] = []
    if topup_path is not None and topup_rows:
        paths.append((topup_path, True))
    paths.extend([(_frozen_roster_path(date), False), (DAY_INV_DIR / f"{date}.json", False), (DAY_INV_DIR / "current.json", False), (DAY_INV_DIR / "latest.json", False), (DAY_INV_DIR / "today.json", False)])
    stats: dict[str, Any] = {"enabled": True, "date_local": date, "path": None, "rows_seen": 0, "loaded": 0, "skipped_wrong_date": 0, "skipped_invalid": 0, "max_matches": max_matches, "source": "direct_day_inventory_file", "frozen_roster": freeze_report, "runtime_topup": topup_report}
    rows: list[dict[str, Any]] = []
    selected_path: Path | None = None
    allow_selected_cross_date = False
    for path, allow_cross_date in paths:
        candidate_rows = _rows_from_inventory_path(path, date, max_matches, allow_cross_date=allow_cross_date)
        if candidate_rows:
            rows = candidate_rows
            selected_path = path
            allow_selected_cross_date = allow_cross_date
            break
    if not rows or selected_path is None:
        stats["reason"] = "inventory_file_missing"
        return [], stats
    stats["path"] = str(selected_path)
    stats["rows_seen"] = len(rows)
    out: list[Any] = []
    for row in rows[:max_matches]:
        if not allow_selected_cross_date and _row_date(row) not in {"", date}:
            stats["skipped_wrong_date"] += 1
            continue
        kickoff_raw = row.get("kickoff_utc") or row.get("commence_time") or row.get("start_time") or row.get("kickoff") or row.get("kickoff_local")
        try:
            commence_time = parse_datetime(kickoff_raw)
        except Exception:
            stats["skipped_invalid"] += 1
            continue
        home = str(row.get("home_team") or row.get("home") or "").strip()
        away = str(row.get("away_team") or row.get("away") or "").strip()
        league = str(row.get("league_name") or row.get("competition") or "").strip()
        if not home or not away or not league:
            stats["skipped_invalid"] += 1
            continue
        source_ids = row.get("source_ids") if isinstance(row.get("source_ids"), dict) else {}
        md = dict(row.get("metadata") or {})
        md.update({
            "day_inventory": True,
            "day_inventory_path": str(selected_path),
            "top_inventory_direct_file_scope": True,
            "day_inventory_coverage": row.get("coverage") or {},
            "day_inventory_refresh": row.get("refresh") or {},
            "day_inventory_source_ids": source_ids,
            "inventory_match_key": row.get("match_key") or row.get("canonical_match_id"),
        })
        source_event_id = (
            str(row.get("source_event_id") or "").strip()
            or str(source_ids.get("odds_api_io") or source_ids.get("bzzoiro") or source_ids.get("football_data") or source_ids.get("thesportsdb") or "").strip()
            or str(row.get("match_key") or row.get("canonical_match_id") or "").strip()
        )
        try:
            out.append(Match(
                source="day_inventory",
                source_event_id=source_event_id,
                sport_key=str(row.get("sport_key") or "soccer"),
                league_name=league,
                home_team=home,
                away_team=away,
                commence_time=commence_time,
                home_team_norm=str(row.get("home_team_norm") or canonicalize_team_name(home)),
                away_team_norm=str(row.get("away_team_norm") or canonicalize_team_name(away)),
                league_key=str(row.get("league_key") or canonicalize_league_name(league)),
                tier=str(row.get("tier") or "mid"),
                metadata=md,
            ))
        except Exception:
            stats["skipped_invalid"] += 1
    stats["loaded"] = len(out)
    return out, stats

def _authoritative_inventory_matches(self: Any, now_utc: Any) -> tuple[list[Any], dict[str, Any]]:
    """Return current top-inventory matches as authoritative run scope."""
    if not _truthy(os.getenv("TOP_INVENTORY_AUTHORITATIVE_RUN_SCOPE", "true"), True):
        return [], {"enabled": False, "reason": "disabled"}

    direct_matches, direct_stats = _direct_day_inventory_matches(self, now_utc)
    if direct_matches:
        if _future_count(direct_matches, now_utc, window_hours=_runtime_window_hours()) < _runtime_min_future_rows() and not (direct_stats.get("runtime_topup") or {}).get("valid"):
            return [], {"enabled": True, "reason": "direct_day_inventory_stale_no_future", "loaded": len(direct_matches), "scoped": 0, **direct_stats}
        scope, info = _inventory_scope()
        scoped = _filter_matches(direct_matches, scope, int(info.get("max_matches") or 300))
        return scoped, {"enabled": True, "reason": "direct_day_inventory", "loaded": len(direct_matches), "scoped": len(scoped), **info, **direct_stats}

    loader = getattr(self, "_load_day_inventory_matches", None)
    if not callable(loader):
        return [], {"enabled": False, "reason": "missing_loader", **direct_stats}
    try:
        matches, stats = loader(now_utc)
    except Exception as exc:
        return [], {"enabled": False, "reason": f"loader_error:{type(exc).__name__}: {exc}", **direct_stats}
    if not isinstance(matches, list) or not matches:
        return [], {"enabled": True, "reason": "no_inventory_matches", **direct_stats, **(dict(stats or {}) if isinstance(stats, dict) else {})}
    scope, info = _inventory_scope()
    scoped = _filter_matches(matches, scope, int(info.get("max_matches") or 300))
    return scoped, {"enabled": True, "reason": "runner_loader_fallback", "loaded": len(matches), "scoped": len(scoped), **info, **(dict(stats or {}) if isinstance(stats, dict) else {})}

def install() -> dict[str, Any]:
    if not _truthy(os.getenv("TOP_INVENTORY_RUNTIME_SCOPE_ENABLED", "true"), True):
        return {"status": "disabled"}
    try:
        from app.services.runner import PredictionRunner
    except Exception as exc:
        return {"status": "error", "error": f"import_runner:{type(exc).__name__}: {exc}"}

    original_fetch_matches = getattr(PredictionRunner, "_fetch_matches", None)
    original_merge_inventory = getattr(PredictionRunner, "_merge_day_inventory_matches", None)
    original_filter_matches = getattr(PredictionRunner, "_filter_matches", None)
    original_fetch_provider = getattr(PredictionRunner, "_fetch_provider", None)
    if not callable(original_fetch_matches):
        return {"status": "missing_fetch_matches"}
    if getattr(original_fetch_matches, _MARKER, False):
        return {"status": "already_wrapped"}

    async def fetch_matches_top_inventory_scoped(self: Any):  # type: ignore[no-untyped-def]
        result = await original_fetch_matches(self)
        if not isinstance(result, tuple) or len(result) < 1:
            return result
        matches, report = _scope_result("fetch_matches", list(result[0] or []), now_utc=datetime.now(UTC))
        meta = result[1] if len(result) > 1 and isinstance(result[1], dict) else {}
        scoped_meta = dict(meta)
        scoped_meta["top_inventory_runtime_scope"] = report
        if len(result) == 1:
            return (matches,)
        return (matches, scoped_meta, *result[2:])

    setattr(fetch_matches_top_inventory_scoped, _MARKER, True)
    setattr(fetch_matches_top_inventory_scoped, "_harizon_original_fetch_matches", original_fetch_matches)
    PredictionRunner._fetch_matches = fetch_matches_top_inventory_scoped  # type: ignore[assignment]

    if callable(original_merge_inventory) and not getattr(original_merge_inventory, _MARKER, False):
        def merge_day_inventory_scoped(self: Any, bootstrap_matches: list[Any], bootstrap_meta: dict[str, Any], now_utc: Any):  # type: ignore[no-untyped-def]
            merged_matches, merged_meta = original_merge_inventory(self, bootstrap_matches, bootstrap_meta, now_utc)
            authoritative, inv_report = _authoritative_inventory_matches(self, now_utc)
            source_matches = authoritative if authoritative else list(merged_matches or [])
            scoped, report = _scope_result(
                "merge_day_inventory",
                list(source_matches or []),
                now_utc=now_utc,
                extra={
                    "authoritative_inventory_scope": bool(authoritative),
                    "authoritative_inventory_loaded": int(inv_report.get("loaded") or 0),
                    "authoritative_inventory_scoped": int(inv_report.get("scoped") or 0),
                    "authoritative_inventory_reason": inv_report.get("reason") or "ok",
                },
            )
            meta = dict(merged_meta or {})
            meta["top_inventory_runtime_scope_after_merge"] = report
            meta["day_inventory_authoritative_scope"] = inv_report
            return scoped, meta

        setattr(merge_day_inventory_scoped, _MARKER, True)
        PredictionRunner._merge_day_inventory_matches = merge_day_inventory_scoped  # type: ignore[assignment]

    if callable(original_filter_matches) and not getattr(original_filter_matches, _MARKER, False):
        def filter_matches_scoped(self: Any, matches: list[Any], now_utc: Any):  # type: ignore[no-untyped-def]
            result = original_filter_matches(self, matches, now_utc)
            if not isinstance(result, tuple) or len(result) < 1:
                return result
            filtered_initial = list(result[0] or [])
            filtering = dict(result[1] or {}) if len(result) > 1 and isinstance(result[1], dict) else {}
            fallback_used = False
            fallback_report: dict[str, Any] = {}
            # If all scoped rows were considered already-started, retry directly
            # from the current top inventory file.  This prevents a stale provider
            # duplicate set from turning a run with future inventory rows into a
            # zero-provider run.
            if not filtered_initial and matches:
                direct_matches, direct_stats = _direct_day_inventory_matches(self, now_utc)
                if direct_matches:
                    retry = original_filter_matches(self, direct_matches, now_utc)
                    if isinstance(retry, tuple) and len(retry) >= 1 and list(retry[0] or []):
                        filtered_initial = list(retry[0] or [])
                        fallback_used = True
                        fallback_filtering = dict(retry[1] or {}) if len(retry) > 1 and isinstance(retry[1], dict) else {}
                        fallback_report = {"direct_inventory_filter_fallback": True, "direct_inventory_stats": direct_stats, "retry_filtering": fallback_filtering}
                        filtering.setdefault("original_zero_filtering", dict(filtering))
                        filtering.update(fallback_filtering)
            scoped, report = _scope_result("filter_matches", filtered_initial, extra=fallback_report if fallback_report else None, now_utc=now_utc)
            if fallback_used:
                report["direct_inventory_filter_fallback"] = True
            filtering["top_inventory_runtime_scope"] = report
            return (scoped, filtering, *result[2:]) if len(result) > 2 else (scoped, filtering)

        setattr(filter_matches_scoped, _MARKER, True)
        PredictionRunner._filter_matches = filter_matches_scoped  # type: ignore[assignment]

    if callable(original_fetch_provider) and not getattr(original_fetch_provider, _MARKER, False):
        async def fetch_provider_scoped(self: Any, provider: Any, method_name: str, *args: Any, **kwargs: Any):  # type: ignore[no-untyped-def]
            if method_name in {"fetch_offers", "fetch_context"} and args and isinstance(args[0], list):
                scoped, _report = _scope_result(f"provider_{method_name}", list(args[0] or []), extra={"provider_method": method_name}, now_utc=datetime.now(UTC))
                args = (scoped, *args[1:])
            return await original_fetch_provider(self, provider, method_name, *args, **kwargs)

        setattr(fetch_provider_scoped, _MARKER, True)
        PredictionRunner._fetch_provider = fetch_provider_scoped  # type: ignore[assignment]

    scope, info = _inventory_scope()
    prune_report = _prune_progressive_state_to_scope(scope, info, "install")
    _append_report("install", {**info, **prune_report, "enabled": True, "installed": True})
    return {"status": "installed", **info, **prune_report, "wrapped": ["_fetch_matches", "_merge_day_inventory_matches", "_filter_matches", "_fetch_provider"]}
