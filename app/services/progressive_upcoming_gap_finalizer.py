from __future__ import annotations

"""Upcoming-window finalizer for progressive coverage.

The first v8 report proved that core coverage is tracked correctly, but the gap
sample still surfaced already-started matches first because negative
`hours_to_kickoff` sorts before upcoming rows. This finalizer keeps historical
state intact but makes the operational plan focus on not-started matches only.

It also lowers retry friction for the two core context providers, Bzzoiro and
SStats, because current coverage shows core odds are much healthier than core
context.
"""

import atexit
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

UTC = timezone.utc
ROOT = Path(__file__).resolve().parents[2]
EXPORT_DIR = ROOT / ".data" / "exports"
PLAN_PATH = EXPORT_DIR / "latest-progressive-coverage-plan.json"
REPORT_PATH = EXPORT_DIR / "latest-progressive-upcoming-gap-finalizer.json"

CORE_CONTEXT = {"bzzoiro", "sstats"}
CORE_ODDS = {"odds_api_io", "bzzoiro", "sstats"}


def _now() -> datetime:
    return datetime.now(UTC)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        if not path.exists():
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_json(path: Path, payload: Any) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except Exception:
        pass


def _parse_dt(value: Any) -> datetime | None:
    try:
        if value in (None, ""):
            return None
        text = str(value)
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)
    except Exception:
        return None


def _hours_to(row: dict[str, Any], now: datetime) -> float | None:
    dt = _parse_dt(row.get("kickoff_utc"))
    if dt is None:
        return None
    return (dt - now).total_seconds() / 3600.0


def _as_int(value: Any) -> int:
    try:
        if value in (None, ""):
            return 0
        return int(float(str(value)))
    except Exception:
        return 0


def _patch_plan_file() -> None:
    plan = _read_json(PLAN_PATH)
    if not plan:
        return
    now = _now()
    sample = plan.get("core_gap_sample") or plan.get("gap_sample") or []
    if not isinstance(sample, list):
        return
    upcoming: list[dict[str, Any]] = []
    started: list[dict[str, Any]] = []
    for row in sample:
        if not isinstance(row, dict):
            continue
        hours = _hours_to(row, now)
        if hours is not None:
            row["hours_to_kickoff"] = round(hours, 2)
        if hours is not None and hours < 0:
            started.append(row)
        else:
            upcoming.append(row)
    upcoming.sort(key=lambda r: (
        r.get("hours_to_kickoff") is None,
        r.get("hours_to_kickoff") if r.get("hours_to_kickoff") is not None else 999999,
        -_as_int(r.get("core_context_needed") or r.get("context_needed")),
        -_as_int(r.get("core_odds_needed") or r.get("odds_needed")),
    ))
    plan["core_gap_sample"] = upcoming[:80]
    plan["gap_sample"] = upcoming[:80]
    plan.setdefault("diagnostics", {})
    plan["diagnostics"].update({
        "upcoming_gap_finalizer": "applied",
        "started_gap_rows_suppressed_from_sample": len(started),
        "upcoming_gap_rows_in_sample": len(upcoming[:80]),
        "reason": "started matches remain in state/history but are not operational targets",
    })
    _write_json(PLAN_PATH, plan)


def install() -> dict[str, Any]:
    payload: dict[str, Any] = {"created_at_utc": _now().isoformat(), "status": "starting"}
    try:
        from app.services import progressive_coverage_runtime_patch as p
    except Exception as exc:
        payload.update({"status": "error", "error": f"import:{type(exc).__name__}: {exc}"})
        _write_json(REPORT_PATH, payload)
        return payload

    old_window_score = getattr(p, "_window_score", None)
    old_stale_bonus = getattr(p, "_stale_bonus", None)
    old_write_plan = getattr(p, "_write_plan_report", None)

    def window_score_upcoming_only(match: Any, now: datetime):
        kickoff = p._match_kickoff(match)
        if kickoff is None:
            return (0, 999999.0)
        hours = (kickoff - now).total_seconds() / 3600.0
        # Already-started matches should never outrank upcoming coverage gaps.
        if hours < 0:
            return (-500, abs(hours))
        window_hours = max(1, p._to_int(os.getenv("CORE_COVERAGE_WINDOW_HOURS") or 4, 4))
        if hours <= window_hours:
            return (135, hours)
        if hours <= window_hours * 2:
            return (100, hours)
        if hours <= 12:
            return (70, hours)
        if hours <= 24:
            return (25, hours)
        return (5, hours)

    def stale_bonus_core_context(row: dict[str, Any], provider: str, now: datetime) -> float:
        provider_l = str(provider or "").lower()
        if callable(old_stale_bonus):
            base = float(old_stale_bonus(row, provider_l, now))
        else:
            base = 0.0
        gap = row.get("coverage_gap") if isinstance(row.get("coverage_gap"), dict) else {}
        context_needed = _as_int(gap.get("core_context_needed") or gap.get("context_needed"))
        odds_needed = _as_int(gap.get("core_odds_needed") or gap.get("odds_needed"))
        # Bzzoiro is both core odds and core context; when any core gap remains,
        # make it retry sooner in the near window.
        if provider_l == "bzzoiro" and (context_needed > 0 or odds_needed > 0):
            return max(base, 18.0)
        if provider_l == "sstats" and context_needed > 0:
            return max(base, 12.0)
        return base

    def write_plan_report_upcoming() -> None:
        if callable(old_write_plan):
            old_write_plan()
        _patch_plan_file()

    p._window_score = window_score_upcoming_only
    p._stale_bonus = stale_bonus_core_context
    p._write_plan_report = write_plan_report_upcoming
    atexit.register(_patch_plan_file)
    _patch_plan_file()
    payload.update({
        "status": "installed",
        "started_matches_deprioritized": True,
        "gap_sample_upcoming_only": True,
        "bzzoiro_core_gap_retry_boost": True,
        "sstats_context_gap_retry_boost": True,
    })
    _write_json(REPORT_PATH, payload)
    return payload
