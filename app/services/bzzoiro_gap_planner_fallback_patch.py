from __future__ import annotations

"""Feed Bzzoiro v2 with real A-tier gap targets when progressive plan is absent.

The source-matrix patch originally looks for latest-progressive-coverage-plan.json.
In current run-bot artifacts that file can be absent, which leaves Bzzoiro with
zero extra gap targets. This patch falls back to the live day inventory and sends
near-future matches that are one Bzzoiro odds source away from A-cover.
"""

import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
EXPORT = ROOT / ".data" / "exports"
DAY_INV = ROOT / ".data" / "day_inventory"
REPORT = EXPORT / "latest-bzzoiro-gap-planner-fallback-patch.json"
_INSTALLED = False
_ORIGINAL_GAP_ROWS = None


def _to_int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ""):
            return default
        if isinstance(value, (list, tuple, set, dict)):
            return len(value)
        return int(float(str(value)))
    except Exception:
        return default


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(str(value).replace(",", "."))
    except Exception:
        return default


def _read_json(path: Path, default: Any) -> Any:
    try:
        if path.exists() and path.stat().st_size > 0:
            return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        pass
    return default


def _write_report(payload: dict[str, Any]) -> None:
    try:
        REPORT.parent.mkdir(parents=True, exist_ok=True)
        REPORT.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except Exception:
        pass


def _sources(row: dict[str, Any], *keys: str) -> set[str]:
    out: set[str] = set()
    containers = [row]
    for key in ("coverage", "metadata", "source_summary"):
        val = row.get(key)
        if isinstance(val, dict):
            containers.append(val)
    for container in containers:
        for key in keys:
            val = container.get(key)
            if isinstance(val, str):
                out.update(x.strip().lower() for x in re.split(r"[,|;/]+", val) if x.strip())
            elif isinstance(val, (list, tuple, set)):
                out.update(str(x).strip().lower() for x in val if str(x).strip())
    return out


def _count(row: dict[str, Any], *keys: str) -> int:
    best = 0
    containers = [row]
    for key in ("coverage", "metadata", "source_summary"):
        val = row.get(key)
        if isinstance(val, dict):
            containers.append(val)
    for container in containers:
        for key in keys:
            best = max(best, _to_int(container.get(key), 0))
    return best


def _dt(row: dict[str, Any]) -> datetime | None:
    for key in ("kickoff_utc", "commence_time", "start_time", "kickoff", "event_date"):
        raw = row.get(key)
        if raw in (None, ""):
            continue
        try:
            text = str(raw).strip().replace(" ", "T")
            if text.endswith("Z"):
                text = text[:-1] + "+00:00"
            if "T" in text and "+" not in text and text.count("-") >= 2:
                text += "+00:00"
            dt = datetime.fromisoformat(text)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            return dt.astimezone(UTC)
        except Exception:
            continue
    return None


def _future(row: dict[str, Any]) -> bool:
    kickoff = _dt(row)
    if kickoff is None:
        return True
    return (kickoff - datetime.now(UTC)).total_seconds() >= -240


def _hours_to_kickoff(row: dict[str, Any]) -> float:
    kickoff = _dt(row)
    if kickoff is None:
        return 999.0
    return (kickoff - datetime.now(UTC)).total_seconds() / 3600.0


def _inventory_rows() -> list[dict[str, Any]]:
    for path in (DAY_INV / "latest.json", DAY_INV / "current.json", DAY_INV / "today.json"):
        payload = _read_json(path, {})
        rows = payload.get("matches") if isinstance(payload, dict) else payload if isinstance(payload, list) else None
        if isinstance(rows, list) and rows:
            return [dict(x) for x in rows if isinstance(x, dict)]
    return []


def _planner_rows() -> list[dict[str, Any]]:
    payload = _read_json(EXPORT / "latest-coverage-planner.json", {})
    rows = payload.get("matches") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return []
    return [dict(x) for x in rows if isinstance(x, dict)]


def _is_gap_target(row: dict[str, Any]) -> bool:
    if not _future(row):
        return False
    odds_sources = _sources(row, "odds_sources", "line_sources", "core_odds_sources")
    context_sources = _sources(row, "context_sources", "context_confirmations", "core_context_sources")
    has_bzz = "bzzoiro" in odds_sources or "bzzoiro_v2" in odds_sources
    odds_count = max(len(odds_sources), _count(row, "odds_sources_count", "independent_odds_sources_count", "line_sources_count", "core_odds_source_count", "odds_source_count"))
    book_count = max(len(_sources(row, "books", "bookmakers")), _count(row, "books_count", "bookmaker_count", "price_confirmation_sources_count", "bookmakers_count"))
    ctx_count = max(len(context_sources), _count(row, "context_sources_count", "confirmation_sources_count", "core_context_source_count", "context_source_count"))
    if not has_bzz and odds_count < 2 and book_count >= 2 and ctx_count >= 1:
        return True
    if not has_bzz and odds_count <= 1 and book_count >= 1 and _hours_to_kickoff(row) <= _to_float(os.getenv("BZZOIRO_V2_PLANNER_FALLBACK_MAX_HOURS"), 24.0):
        return True
    return False


def _fallback_gap_rows() -> list[dict[str, Any]]:
    limit = max(1, _to_int(os.getenv("BZZOIRO_V2_PLANNER_FALLBACK_TARGET_LIMIT"), 180))
    rows = _inventory_rows()
    source = "day_inventory"
    if not rows:
        rows = _planner_rows()
        source = "coverage_planner"
    candidates = [row for row in rows if _is_gap_target(row)]
    candidates.sort(key=lambda r: (_hours_to_kickoff(r), -_count(r, "books_count", "bookmaker_count", "price_confirmation_sources_count")))
    out = candidates[:limit]
    _write_report({
        "status": "ok",
        "created_at_utc": datetime.now(UTC).isoformat(),
        "source": source if rows else "none",
        "rows_seen": len(rows),
        "gap_candidates": len(candidates),
        "returned": len(out),
        "limit": limit,
        "sample": [
            {
                "match_key": row.get("match_key") or row.get("canonical_match_id"),
                "home_team": row.get("home_team") or row.get("home"),
                "away_team": row.get("away_team") or row.get("away"),
                "kickoff_utc": row.get("kickoff_utc") or row.get("commence_time"),
                "odds_sources": sorted(_sources(row, "odds_sources", "line_sources")),
                "books_count": _count(row, "books_count", "price_confirmation_sources_count"),
                "context_sources_count": _count(row, "context_sources_count", "confirmation_sources_count"),
            }
            for row in out[:20]
        ],
    })
    return out


def install() -> dict[str, Any]:
    global _INSTALLED, _ORIGINAL_GAP_ROWS
    if _INSTALLED:
        return {"status": "already_installed"}
    os.environ.setdefault("BZZOIRO_V2_SOURCE_MATRIX_TARGET_LIMIT", os.getenv("BZZOIRO_CONTEXT_GAP_MATCH_LIMIT") or "220")
    os.environ.setdefault("BZZOIRO_CONTEXT_GAP_MATCH_LIMIT", "220")
    os.environ.setdefault("BZZOIRO_V2_ODDS_COMPARISON_MATCH_LIMIT", "90")
    os.environ.setdefault("BZZOIRO_V2_ODDS_COMPARISON_MAX_REQUESTS", os.getenv("BZZOIRO_V2_ODDS_COMPARISON_MATCH_LIMIT") or "90")
    try:
        from app.services import bzzoiro_v2_source_matrix_runtime_patch as matrix
    except Exception as exc:
        payload = {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
        _write_report(payload)
        return payload
    original = getattr(matrix, "_gap_rows", None)
    _ORIGINAL_GAP_ROWS = original

    def gap_rows_with_inventory_fallback() -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        if callable(original):
            try:
                rows = [dict(x) for x in (original() or []) if isinstance(x, dict)]
            except Exception:
                rows = []
        if rows:
            _write_report({"status": "original_plan_used", "created_at_utc": datetime.now(UTC).isoformat(), "returned": len(rows)})
            return rows
        return _fallback_gap_rows()

    matrix._gap_rows = gap_rows_with_inventory_fallback  # type: ignore[attr-defined]
    _INSTALLED = True
    payload = {"status": "installed", "created_at_utc": datetime.now(UTC).isoformat(), "policy": "fallback Bzzoiro v2 gap targets from day inventory when progressive plan is missing"}
    _write_report(payload)
    return payload
