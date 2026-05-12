from __future__ import annotations

"""Upcoming-window finalizer for progressive coverage.

This finalizer makes the operational gap plan focus on not-started matches. The
first version filtered `core_gap_sample` after it had already been truncated to
80 rows. Late-day runs could therefore suppress ~78 started rows and leave only
2 upcoming gaps visible, even when many more upcoming gaps existed in the full
state.

This version rebuilds the upcoming gap sample from the full progressive state.
Started matches remain in history/state, but they no longer consume the visible
sample or provider priority.
"""

import atexit
import json
import os
from collections import Counter
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


def _as_int(value: Any) -> int:
    try:
        if value in (None, ""):
            return 0
        return int(float(str(value)))
    except Exception:
        return 0


def _tokens(value: Any) -> set[str]:
    tokens: set[str] = set()
    if value in (None, ""):
        return tokens
    if isinstance(value, str):
        tokens.update(x.strip().lower() for x in value.replace(";", ",").replace("|", ",").split(",") if x.strip())
    elif isinstance(value, dict):
        tokens.update(str(k).strip().lower() for k, v in value.items() if v not in (None, "", False, [], {}))
    elif isinstance(value, (list, tuple, set)):
        for item in value:
            tokens |= _tokens(item)
    return {t for t in tokens if t}


def _rebuild_plan_from_state(p: Any) -> None:
    plan = _read_json(PLAN_PATH)
    state = p._load_state()
    matches = state.get("matches") if isinstance(state.get("matches"), dict) else {}
    if not matches:
        return

    now = _now()
    min_odds = max(1, _as_int(os.getenv("PROGRESSIVE_COVERAGE_MIN_ODDS_SOURCES") or 2))
    min_context = max(1, _as_int(os.getenv("PROGRESSIVE_COVERAGE_MIN_CONTEXT_SOURCES") or 2))
    counts = Counter()
    upcoming_gaps: list[dict[str, Any]] = []
    started_gap_rows = 0
    all_gap_rows = 0

    for key, row in matches.items():
        if not isinstance(row, dict):
            continue
        odds = _tokens(row.get("odds_sources"))
        context = _tokens(row.get("context_sources"))
        core_odds = odds & CORE_ODDS
        core_context = context & CORE_CONTEXT
        odds_count = len(core_odds)
        context_count = len(core_context)
        counts["matches_tracked"] += 1
        counts["core_odds_1plus"] += int(odds_count >= 1)
        counts["core_odds_2plus"] += int(odds_count >= min_odds)
        counts["core_context_1plus"] += int(context_count >= 1)
        counts["core_context_2plus"] += int(context_count >= min_context)
        counts["core_ready_2plus_both"] += int(odds_count >= min_odds and context_count >= min_context)
        counts["all_odds_1plus"] += int(len(odds) >= 1)
        counts["all_odds_2plus"] += int(len(odds) >= min_odds)
        counts["all_context_1plus"] += int(len(context) >= 1)
        counts["all_context_2plus"] += int(len(context) >= min_context)
        # Backward-compatible names intentionally mean core coverage.
        counts["odds_1plus"] += int(odds_count >= 1)
        counts["odds_2plus"] += int(odds_count >= min_odds)
        counts["context_1plus"] += int(context_count >= 1)
        counts["context_2plus"] += int(context_count >= min_context)
        counts["ready_2plus_both"] += int(odds_count >= min_odds and context_count >= min_context)

        kickoff = _parse_dt(row.get("kickoff_utc"))
        hours = (kickoff - now).total_seconds() / 3600.0 if kickoff else None
        if hours is not None and 0 <= hours <= 4:
            counts["window_0_4h"] += 1
            counts["window_0_4h_core_ready_2plus_both"] += int(odds_count >= min_odds and context_count >= min_context)
            counts["window_0_4h_ready_2plus_both"] += int(odds_count >= min_odds and context_count >= min_context)
        if hours is not None and 0 <= hours <= 12:
            counts["window_0_12h"] += 1
            counts["window_0_12h_core_ready_2plus_both"] += int(odds_count >= min_odds and context_count >= min_context)
            counts["window_0_12h_ready_2plus_both"] += int(odds_count >= min_odds and context_count >= min_context)

        odds_needed = max(0, min_odds - odds_count)
        context_needed = max(0, min_context - context_count)
        if odds_needed <= 0 and context_needed <= 0:
            continue
        all_gap_rows += 1
        if hours is not None and hours < 0:
            started_gap_rows += 1
            continue
        upcoming_gaps.append({
            "match_key": key,
            "home_team": row.get("home_team"),
            "away_team": row.get("away_team"),
            "kickoff_utc": row.get("kickoff_utc"),
            "hours_to_kickoff": round(hours, 2) if hours is not None else None,
            "core_odds_sources": sorted(core_odds),
            "core_context_sources": sorted(core_context),
            "supplemental_odds_sources": sorted(odds - core_odds),
            "supplemental_context_sources": sorted(context - core_context),
            "core_odds_needed": odds_needed,
            "core_context_needed": context_needed,
        })

    upcoming_gaps.sort(key=lambda r: (
        r.get("hours_to_kickoff") is None,
        r.get("hours_to_kickoff") if r.get("hours_to_kickoff") is not None else 999999,
        -_as_int(r.get("core_context_needed")),
        -_as_int(r.get("core_odds_needed")),
    ))

    plan.setdefault("contract", {
        "core_context_providers": sorted(CORE_CONTEXT),
        "core_odds_providers": sorted(CORE_ODDS),
        "core_providers": sorted(CORE_CONTEXT | CORE_ODDS),
    })
    plan["counts"] = dict(counts)
    plan["core_gap_sample"] = upcoming_gaps[:80]
    plan["gap_sample"] = upcoming_gaps[:80]
    plan["created_at_utc"] = now.isoformat()
    plan["enabled"] = True
    plan.setdefault("diagnostics", {})
    plan["diagnostics"].update({
        "upcoming_gap_finalizer": "applied_full_state_rebuild",
        "all_gap_rows_before_started_filter": all_gap_rows,
        "started_gap_rows_suppressed_from_sample": started_gap_rows,
        "upcoming_gap_rows_total": len(upcoming_gaps),
        "upcoming_gap_rows_in_sample": len(upcoming_gaps[:80]),
        "reason": "started matches remain in state/history but are not operational targets; sample rebuilt from full state",
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

    old_stale_bonus = getattr(p, "_stale_bonus", None)
    old_write_plan = getattr(p, "_write_plan_report", None)

    def window_score_upcoming_only(match: Any, now: datetime):
        kickoff = p._match_kickoff(match)
        if kickoff is None:
            return (0, 999999.0)
        hours = (kickoff - now).total_seconds() / 3600.0
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
        if provider_l == "bzzoiro" and (context_needed > 0 or odds_needed > 0):
            return max(base, 18.0)
        if provider_l == "sstats" and context_needed > 0:
            return max(base, 12.0)
        return base

    def write_plan_report_upcoming() -> None:
        if callable(old_write_plan):
            old_write_plan()
        _rebuild_plan_from_state(p)

    p._window_score = window_score_upcoming_only
    p._stale_bonus = stale_bonus_core_context
    p._write_plan_report = write_plan_report_upcoming
    atexit.register(lambda: _rebuild_plan_from_state(p))
    _rebuild_plan_from_state(p)
    payload.update({
        "status": "installed",
        "started_matches_deprioritized": True,
        "gap_sample_upcoming_only": True,
        "gap_sample_rebuilt_from_full_state": True,
        "bzzoiro_core_gap_retry_boost": True,
        "sstats_context_gap_retry_boost": True,
    })
    _write_json(REPORT_PATH, payload)
    return payload
