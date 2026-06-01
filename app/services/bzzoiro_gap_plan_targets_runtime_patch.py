from __future__ import annotations

"""Force Bzzoiro gap-pass to see progressive 2+/2+ gap matches.

The report can show `Bzzoiro gap targets 0` even while
latest-progressive-coverage-plan.json has many `core_context_needed > 0` rows.
That happens when the runner passes only the small current enrichment selection
into BzzoiroContextProvider.fetch_context.  This runtime wrapper appends synthetic
Match objects from the progressive gap plan before the provider-level gap
finalizer runs, without relaxing any quality gate.
"""

import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.schemas import Match
from app.utils import normalize_text

_INSTALLED = False
_ORIGINAL_FETCH_CONTEXT = None
ROOT = Path(__file__).resolve().parents[2]
EXPORT_DIR = ROOT / ".data" / "exports"
PLAN_PATH = EXPORT_DIR / "latest-progressive-coverage-plan.json"
INSTALL_REPORT = EXPORT_DIR / "latest-bzzoiro-gap-plan-targets-install.json"
RUNTIME_REPORT = EXPORT_DIR / "latest-bzzoiro-gap-plan-targets-runtime.json"


def _truthy(value: Any, default: bool = False) -> bool:
    if value in (None, ""):
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


def _read_json(path: Path) -> dict[str, Any]:
    try:
        if not path.exists() or path.stat().st_size <= 0:
            return {}
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def _parse_dt(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        text = str(value).strip().replace(" ", "T")
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        if "T" in text and "+" not in text and text.count("-") >= 2:
            text += "+00:00"
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)
    except Exception:
        return None


def _league_key(value: Any) -> str:
    text = normalize_text(str(value or ""))
    return re.sub(r"[^a-z0-9_]+", "_", text).strip("_") or "unknown"


def _match_from_gap_row(row: dict[str, Any]) -> Match | None:
    home = str(row.get("home_team") or row.get("home") or "").strip()
    away = str(row.get("away_team") or row.get("away") or "").strip()
    kickoff = _parse_dt(row.get("kickoff_utc") or row.get("commence_time") or row.get("event_date"))
    if not home or not away or kickoff is None:
        return None
    league = str(row.get("league_name") or row.get("league") or row.get("competition") or "Unknown").strip() or "Unknown"
    metadata = {
        "source_matrix_forced_bzzoiro_gap_target": True,
        "progressive_gap_row": row,
        "core_context_needed": _to_int(row.get("core_context_needed"), 0),
        "core_odds_needed": _to_int(row.get("core_odds_needed"), 0),
        "core_context_sources": row.get("core_context_sources") or [],
        "core_odds_sources": row.get("core_odds_sources") or [],
    }
    return Match(
        source="progressive_gap_plan",
        source_event_id=str(row.get("source_event_id") or row.get("event_id") or row.get("match_key") or ""),
        sport_key="soccer",  # type: ignore[arg-type]
        league_name=league,
        home_team=home,
        away_team=away,
        commence_time=kickoff,
        home_team_norm=normalize_text(home),
        away_team_norm=normalize_text(away),
        league_key=_league_key(league),
        tier=str(row.get("tier") or "mid"),
        metadata=metadata,
    )


def _gap_plan_matches(existing_keys: set[str]) -> tuple[list[Match], dict[str, Any]]:
    plan = _read_json(PLAN_PATH)
    rows = plan.get("core_gap_sample") or plan.get("gap_sample") or []
    if not isinstance(rows, list):
        rows = []
    now = datetime.now(UTC)
    limit = max(0, _to_int(os.getenv("BZZOIRO_FORCE_GAP_TARGET_LIMIT") or os.getenv("SOURCE_MATRIX_GAP_APPEND_LIMIT") or 180, 180))
    out: list[Match] = []
    inspected = 0
    skipped_no_context_gap = 0
    skipped_old = 0
    skipped_duplicate = 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        inspected += 1
        if _to_int(row.get("core_context_needed") or row.get("context_needed"), 0) <= 0:
            skipped_no_context_gap += 1
            continue
        existing_ctx = {str(x).strip().lower() for x in (row.get("core_context_sources") or []) if str(x).strip()}
        if "bzzoiro" in existing_ctx:
            skipped_no_context_gap += 1
            continue
        match = _match_from_gap_row(row)
        if match is None:
            continue
        try:
            if (match.commence_time.astimezone(UTC) - now).total_seconds() < -180:
                skipped_old += 1
                continue
        except Exception:
            pass
        key = getattr(match, "match_key", "")
        if key in existing_keys:
            skipped_duplicate += 1
            continue
        existing_keys.add(key)
        out.append(match)
        if limit and len(out) >= limit:
            break
    return out, {
        "plan_path": str(PLAN_PATH),
        "inspected": inspected,
        "appended": len(out),
        "limit": limit,
        "skipped_no_context_gap_or_has_bzzoiro": skipped_no_context_gap,
        "skipped_old": skipped_old,
        "skipped_duplicate": skipped_duplicate,
        "sample": [getattr(m, "match_key", "") for m in out[:25]],
    }


async def _fetch_context_with_forced_gap_targets(self: Any, matches: list[Match]):  # type: ignore[no-untyped-def]
    original = _ORIGINAL_FETCH_CONTEXT
    if not callable(original) or not _truthy(os.getenv("BZZOIRO_FORCE_GAP_PLAN_TARGETS"), True):
        return await original(self, matches) if callable(original) else ({}, {}, {})
    base_matches = list(matches or [])
    existing_keys = {getattr(m, "match_key", "") for m in base_matches if getattr(m, "match_key", "")}
    forced, report = _gap_plan_matches(existing_keys)
    expanded = base_matches + forced
    _write_json(RUNTIME_REPORT, {
        "created_at_utc": datetime.now(UTC).isoformat(),
        "input_matches": len(base_matches),
        "forced_matches": len(forced),
        "expanded_matches": len(expanded),
        **report,
    })
    return await original(self, expanded)


def install() -> dict[str, Any]:
    global _INSTALLED, _ORIGINAL_FETCH_CONTEXT
    if _INSTALLED:
        return {"installed": True, "already_installed": True}
    try:
        from app.providers.bzzoiro import BzzoiroContextProvider
    except Exception as exc:
        payload = {"installed": False, "error": f"import:{type(exc).__name__}: {exc}"}
        _write_json(INSTALL_REPORT, payload)
        return payload
    current = BzzoiroContextProvider.fetch_context
    if getattr(current, "_harizon_bzzoiro_gap_plan_targets", False):
        payload = {"installed": True, "already_installed": True}
        _write_json(INSTALL_REPORT, payload)
        return payload
    _ORIGINAL_FETCH_CONTEXT = current
    _fetch_context_with_forced_gap_targets._harizon_bzzoiro_gap_plan_targets = True  # type: ignore[attr-defined]
    BzzoiroContextProvider.fetch_context = _fetch_context_with_forced_gap_targets  # type: ignore[assignment]
    os.environ.setdefault("BZZOIRO_FORCE_GAP_PLAN_TARGETS", "true")
    os.environ.setdefault("BZZOIRO_FORCE_GAP_TARGET_LIMIT", os.getenv("SOURCE_MATRIX_GAP_APPEND_LIMIT", "180"))
    _INSTALLED = True
    payload = {
        "installed": True,
        "enabled": _truthy(os.getenv("BZZOIRO_FORCE_GAP_PLAN_TARGETS"), True),
        "target_limit": _to_int(os.getenv("BZZOIRO_FORCE_GAP_TARGET_LIMIT"), 180),
        "wrapped_current": getattr(current, "__name__", "unknown"),
    }
    _write_json(INSTALL_REPORT, payload)
    return payload
