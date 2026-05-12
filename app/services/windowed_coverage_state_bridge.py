from __future__ import annotations

"""Bridge progressive coverage state into windowed publication audit.

Live logs showed that progressive coverage already knew useful sources for near
matches, while the windowed CandidateFactory audit saw only the sources attached
inside the current in-memory candidate. This made the audit stricter than the
same run's coverage state:
- progressive: odds_api_io + sstats, context sstats/weather/openligadb;
- windowed audit: odds_api_io only, sometimes no context.

This patch does not relax EV, probability, xG, price sanity, or Telegram safety.
It only lets windowed audit reuse already-computed progressive coverage evidence
and allows final-pre-kickoff runs to skip an impossible future cron snapshot when
refresh_plan.no_more_regular_run_before_kickoff is true.
"""

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

UTC = timezone.utc
ROOT = Path(__file__).resolve().parents[2]
EXPORT_DIR = ROOT / ".data" / "exports"
PLAN_PATH = EXPORT_DIR / "latest-progressive-coverage-plan.json"
REFRESH_PLAN_PATH = EXPORT_DIR / "latest-day-inventory-refresh-plan.json"
REPORT_PATH = EXPORT_DIR / "latest-windowed-coverage-state-bridge.json"
_INSTALLED = False

CORE_PROVIDERS = {"odds_api_io", "sstats", "bzzoiro"}
SUPPLEMENTAL_CONTEXT = {
    "weather", "open_meteo", "openligadb", "thesportsdb", "football_data",
    "clubelo", "allsportsapi", "api_football", "sportlogic", "futrixmetrics",
}


def _read_json(path: Path) -> dict[str, Any]:
    try:
        if not path.exists() or path.stat().st_size <= 0:
            return {}
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except Exception:
        pass


def _norm(value: Any) -> str:
    text = re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")
    if text.startswith("odds_api_io") or text in {"oddsapiio", "odds_api"}:
        return "odds_api_io"
    if "bzzoiro" in text or text.startswith("bsd"):
        return "bzzoiro"
    if "sstats" in text:
        return "sstats"
    if "open_meteo" in text or "openmeteo" in text or text == "weatherapi":
        return "weather"
    if text == "open_ligadb":
        return "openligadb"
    if "sportsdb" in text:
        return "thesportsdb"
    return text


def _tokens(value: Any) -> set[str]:
    out: set[str] = set()
    if value in (None, ""):
        return out
    if isinstance(value, str):
        parts = re.split(r"[,;|+/\s]+", value)
    elif isinstance(value, dict):
        parts = list(value.keys()) + [x for x in value.values() if isinstance(x, str)]
    elif isinstance(value, (list, tuple, set)):
        parts = list(value)
    else:
        parts = [value]
    for item in parts:
        token = _norm(item)
        if token:
            out.add(token)
    return out


def _canon_team(value: Any) -> str:
    text = str(value or "").lower()
    text = text.replace("fc", " ").replace("cf", " ").replace("cd", " ")
    text = re.sub(r"\b(club|ca|sc|de|do|da|the|real)\b", " ", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def _match_aliases(home: Any, away: Any, kickoff: Any = None) -> set[str]:
    h = _canon_team(home)
    a = _canon_team(away)
    date = str(kickoff or "")[:10]
    out = {f"{h}|{a}", f"{a}|{h}"}
    if date:
        out.add(f"{date}|{h}|{a}")
        out.add(f"{date}|{a}|{h}")
    return {x for x in out if x and x != "|"}


def _build_plan_index() -> dict[str, dict[str, Any]]:
    plan = _read_json(PLAN_PATH)
    rows = []
    for key in ("core_gap_sample", "gap_sample"):
        value = plan.get(key)
        if isinstance(value, list):
            rows.extend([x for x in value if isinstance(x, dict)])
    index: dict[str, dict[str, Any]] = {}
    for row in rows:
        keys = {str(row.get("match_key") or "").strip()}
        keys |= _match_aliases(row.get("home_team"), row.get("away_team"), row.get("kickoff_utc"))
        for key in keys:
            if key:
                index.setdefault(key, row)
    return index


def _build_final_pre_kickoff_index() -> set[str]:
    payload = _read_json(REFRESH_PLAN_PATH)
    rows = payload.get("top_priority_matches") if isinstance(payload.get("top_priority_matches"), list) else []
    keys: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        refresh = row.get("refresh_plan") if isinstance(row.get("refresh_plan"), dict) else {}
        if not refresh.get("no_more_regular_run_before_kickoff"):
            continue
        keys.add(str(row.get("match_key") or "").strip())
        keys |= _match_aliases(row.get("home_team"), row.get("away_team"), row.get("kickoff_utc"))
    return {k for k in keys if k}


def _candidate_keys(candidate: Any) -> set[str]:
    keys = {str(getattr(candidate, "match_key", "") or "").strip()}
    keys |= _match_aliases(
        getattr(candidate, "home_team", ""),
        getattr(candidate, "away_team", ""),
        getattr(getattr(candidate, "commence_time", None), "isoformat", lambda: "")(),
    )
    return {x for x in keys if x}


def _plan_row_for(candidate: Any, index: dict[str, dict[str, Any]]) -> dict[str, Any]:
    for key in _candidate_keys(candidate):
        row = index.get(key)
        if isinstance(row, dict):
            return row
    return {}


def _context_equivalent_sources(context_sources: set[str], plan_row: dict[str, Any]) -> set[str]:
    out = set(context_sources)
    out |= _tokens(plan_row.get("core_context_sources"))
    out |= _tokens(plan_row.get("supplemental_context_sources"))
    # SStats + any independent supplemental context is valid coverage evidence
    # for model audit when Bzzoiro is unavailable for the league. It is not marked
    # as a bookmaker/price source.
    if "sstats" in out and (out & SUPPLEMENTAL_CONTEXT):
        out.add("context_equiv_supplemental")
    return out


def _odds_equivalent_sources(odds_sources: set[str], plan_row: dict[str, Any]) -> set[str]:
    out = set(odds_sources)
    out |= _tokens(plan_row.get("core_odds_sources"))
    out |= _tokens(plan_row.get("supplemental_odds_sources"))
    return out


def _apply_bridge_to_candidate(candidate: Any, plan_index: dict[str, dict[str, Any]], final_keys: set[str]) -> dict[str, Any] | None:
    summary = getattr(candidate, "source_summary", None)
    if not isinstance(summary, dict):
        return None
    coverage = summary.get("windowed_core_coverage")
    if not isinstance(coverage, dict):
        return None
    plan_row = _plan_row_for(candidate, plan_index)
    before = {
        "odds_sources": list(coverage.get("odds_sources") or []),
        "context_sources": list(coverage.get("context_sources") or []),
        "reject_reasons": list(coverage.get("reject_reasons") or []),
        "movement": dict(coverage.get("movement") or {}) if isinstance(coverage.get("movement"), dict) else {},
    }
    odds_sources = _odds_equivalent_sources(_tokens(coverage.get("odds_sources")), plan_row)
    context_sources = _context_equivalent_sources(_tokens(coverage.get("context_sources")), plan_row)
    core_count = len((odds_sources | context_sources) & CORE_PROVIDERS)
    reject_reasons = list(coverage.get("reject_reasons") or [])
    reject_reasons = [r for r in reject_reasons if not (r == "odds_sources_below_2" and len(odds_sources) >= 2)]
    reject_reasons = [r for r in reject_reasons if not (r == "context_sources_below_2" and len(context_sources) >= 2)]
    reject_reasons = [r for r in reject_reasons if not (r == "core_api_coverage_below_2_of_3" and core_count >= 2)]

    movement = dict(coverage.get("movement") or {}) if isinstance(coverage.get("movement"), dict) else {}
    is_final = bool(_candidate_keys(candidate) & final_keys)
    if is_final and movement.get("reason") == "needs_next_cron_line_movement_recheck":
        movement.update({
            "ok": True,
            "reason": "final_pre_kickoff_no_more_regular_run",
            "history_required": False,
            "relieved_by": "latest-day-inventory-refresh-plan.no_more_regular_run_before_kickoff",
        })
        reject_reasons = [r for r in reject_reasons if r != "needs_next_cron_line_movement_recheck"]

    coverage["odds_sources"] = sorted(odds_sources)
    coverage["context_sources"] = sorted(context_sources)
    coverage["core_provider_count"] = core_count
    coverage["movement"] = movement
    coverage["reject_reasons"] = reject_reasons
    coverage["accepted"] = not reject_reasons
    coverage["state_bridge"] = {
        "applied": True,
        "plan_match_key": plan_row.get("match_key"),
        "final_pre_kickoff_relief": is_final,
        "before": before,
    }
    summary["windowed_core_coverage"] = coverage
    # Keep explicit non-price line sources separate from true bookmaker source
    # fields. Downstream Telegram safety can still require real odds sources.
    summary["line_sources"] = sorted(odds_sources)
    summary["context_sources"] = sorted(context_sources)
    return {
        "match_key": getattr(candidate, "match_key", ""),
        "home": getattr(candidate, "home_team", ""),
        "away": getattr(candidate, "away_team", ""),
        "accepted": coverage["accepted"],
        "reject_reasons": reject_reasons,
        "odds_sources": sorted(odds_sources),
        "context_sources": sorted(context_sources),
        "movement_reason": movement.get("reason"),
        "plan_match_key": plan_row.get("match_key"),
    }


def _patch_candidate_factory(report: dict[str, Any]) -> None:
    from app.services.model import CandidateFactory

    current = CandidateFactory.build_candidates
    if getattr(current, "_harizon_windowed_state_bridge", False):
        report["candidate_factory"] = "already_wrapped"
        return

    def build_candidates_with_state_bridge(self, matches, offers_by_match, contexts_by_match, market_signals_by_match=None):  # type: ignore[no-untyped-def]
        candidates, rejections, debug = current(
            self,
            matches,
            offers_by_match,
            contexts_by_match,
            market_signals_by_match=market_signals_by_match,
        )
        plan_index = _build_plan_index()
        final_keys = _build_final_pre_kickoff_index()
        changed: list[dict[str, Any]] = []
        accepted = 0
        for candidate in list(candidates or []):
            row = _apply_bridge_to_candidate(candidate, plan_index, final_keys)
            if row is not None:
                changed.append(row)
                accepted += 1 if row.get("accepted") else 0
        if changed:
            debug = dict(debug or {})
            debug["windowed_coverage_state_bridge"] = {
                "candidates_seen": len(candidates or []),
                "candidates_changed": len(changed),
                "accepted_after_bridge": accepted,
                "sample": changed[:20],
            }
            # Recompute only this audit counter so diagnostics reflect bridge.
            if isinstance(rejections, dict):
                still_blocked = len([x for x in changed if not x.get("accepted")])
                if still_blocked > 0:
                    rejections["windowed_core_publish_block"] = still_blocked
                else:
                    rejections.pop("windowed_core_publish_block", None)
            _write_json(REPORT_PATH, {
                "created_at_utc": datetime.now(UTC).isoformat(),
                "stage": "candidate_factory_bridge",
                "candidates_seen": len(candidates or []),
                "candidates_changed": len(changed),
                "accepted_after_bridge": accepted,
                "final_pre_kickoff_keys": len(final_keys),
                "plan_index_keys": len(plan_index),
                "sample": changed[:30],
            })
        return candidates, rejections, debug

    build_candidates_with_state_bridge._harizon_windowed_state_bridge = True  # type: ignore[attr-defined]
    CandidateFactory.build_candidates = build_candidates_with_state_bridge  # type: ignore[assignment]
    report["candidate_factory"] = "wrapped"


def _patch_publish_filter(report: dict[str, Any]) -> None:
    from app.services.runner import PredictionRunner

    current = getattr(PredictionRunner, "_filter_publishable_candidates", None)
    if not callable(current):
        report["publish_filter"] = "missing"
        return
    if getattr(current, "_harizon_windowed_state_bridge", False):
        report["publish_filter"] = "already_wrapped"
        return

    def publish_filter_with_state_bridge(self, candidates):  # type: ignore[no-untyped-def]
        # CandidateFactory bridge should already have rewritten coverage. This is
        # a safety pass for any candidates built before the wrapper was installed.
        plan_index = _build_plan_index()
        final_keys = _build_final_pre_kickoff_index()
        for candidate in list(candidates or []):
            _apply_bridge_to_candidate(candidate, plan_index, final_keys)
        return current(self, candidates)

    publish_filter_with_state_bridge._harizon_windowed_state_bridge = True  # type: ignore[attr-defined]
    PredictionRunner._filter_publishable_candidates = publish_filter_with_state_bridge  # type: ignore[assignment]
    report["publish_filter"] = "wrapped"


def install() -> dict[str, Any]:
    global _INSTALLED
    if _INSTALLED:
        return {"status": "already_installed"}
    _INSTALLED = True
    report: dict[str, Any] = {
        "created_at_utc": datetime.now(UTC).isoformat(),
        "status": "starting",
        "plan_path": str(PLAN_PATH),
        "refresh_plan_path": str(REFRESH_PLAN_PATH),
    }
    try:
        _patch_candidate_factory(report)
        _patch_publish_filter(report)
        report["status"] = "installed"
    except Exception as exc:
        report["status"] = "error"
        report["error"] = f"{type(exc).__name__}: {exc}"
    _write_json(REPORT_PATH, report)
    return report
