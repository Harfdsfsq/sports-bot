from __future__ import annotations

"""Controlled quality relief for consensus-safe candidates.

Live runs reached this state:
- final API consensus/source guard works;
- candidates_before_quality > 0;
- quality rejects all remaining candidates by post-calibration probability guard.

We must not publish negative EV or single-source picks. This wrapper only rescues
candidates that have already passed the final consensus/source layer and still
have non-negative consensus EV/edge after repricing.
"""

import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
REPORT_PATH = ROOT / ".data" / "exports" / "latest-quality-consensus-safe-relief.json"
_INSTALLED = False


def _truthy(value: Any, default: bool = False) -> bool:
    raw = str(value if value is not None else "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on", "force"}


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        number = float(str(value).replace(",", "."))
        return number if math.isfinite(number) else default
    except Exception:
        return default


def _as_int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ""):
            return default
        return int(float(str(value).replace(",", ".")))
    except Exception:
        return default


def _write(payload: dict[str, Any]) -> None:
    try:
        REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        REPORT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except Exception:
        pass


def _summary(candidate: Any) -> dict[str, Any]:
    value = getattr(candidate, "source_summary", None)
    return value if isinstance(value, dict) else {}


def _diagnostics(candidate: Any) -> dict[str, Any]:
    value = getattr(candidate, "diagnostics", None)
    return value if isinstance(value, dict) else {}


def _coverage(candidate: Any) -> dict[str, Any]:
    summary = _summary(candidate)
    diag = _diagnostics(candidate)
    cov = summary.get("api_coverage_consensus") or diag.get("api_coverage_consensus")
    if isinstance(cov, dict):
        return cov
    report = getattr(candidate, "integrity_report", None)
    if isinstance(report, dict):
        cov = report.get("api_coverage_consensus")
        if isinstance(cov, dict):
            return cov
    # Some wrappers flatten these fields directly into source_summary.
    return summary


def _context_sources_count(candidate: Any) -> int:
    summary = _summary(candidate)
    cov = _coverage(candidate)
    for key in ("context_sources_count", "exact_context_sources_count"):
        value = _as_int(summary.get(key) if isinstance(summary, dict) else None, 0)
        if value:
            return value
        value = _as_int(cov.get(key) if isinstance(cov, dict) else None, 0)
        if value:
            return value
    value = summary.get("context_sources") if isinstance(summary, dict) else None
    if isinstance(value, (list, tuple, set)):
        return len({str(x).strip().lower() for x in value if str(x).strip()})
    windowed = summary.get("windowed_core_coverage") if isinstance(summary, dict) else None
    if isinstance(windowed, dict):
        value = windowed.get("context_sources")
        if isinstance(value, (list, tuple, set)):
            return len({str(x).strip().lower() for x in value if str(x).strip()})
    return 0


def _odds_sources_count(candidate: Any) -> int:
    cov = _coverage(candidate)
    summary = _summary(candidate)
    for key in ("exact_odds_sources_count", "odds_sources_count", "price_sources_count"):
        value = _as_int(cov.get(key) if isinstance(cov, dict) else None, 0)
        if value:
            return value
        value = _as_int(summary.get(key) if isinstance(summary, dict) else None, 0)
        if value:
            return value
    for key in ("exact_odds_sources", "odds_sources", "price_sources"):
        value = cov.get(key) if isinstance(cov, dict) else None
        if isinstance(value, (list, tuple, set)):
            return len({str(x).strip().lower() for x in value if str(x).strip()})
        value = summary.get(key) if isinstance(summary, dict) else None
        if isinstance(value, (list, tuple, set)):
            return len({str(x).strip().lower() for x in value if str(x).strip()})
    return _as_int(getattr(candidate, "sources_count", None), 0)


def _books_count(candidate: Any) -> int:
    cov = _coverage(candidate)
    for key in ("exact_books_count", "exact_line_bookmakers_count"):
        value = _as_int(cov.get(key) if isinstance(cov, dict) else None, 0)
        if value:
            return value
    return _as_int(getattr(candidate, "books_count", None), 0)


def _canonical_ev_edge(candidate: Any) -> tuple[float, float]:
    odds = _as_float(getattr(candidate, "selected_odds", None), 0.0) or _as_float(getattr(candidate, "odds", None), 0.0)
    probability = (
        _as_float(getattr(candidate, "probability_used_for_ev", None), 0.0)
        or _as_float(getattr(candidate, "canonical_adjusted_probability", None), 0.0)
        or _as_float(getattr(candidate, "adjusted_probability", None), 0.0)
        or _as_float(getattr(candidate, "final_probability", None), 0.0)
        or _as_float(getattr(candidate, "model_probability", None), 0.0)
    )
    implied = 1.0 / odds if odds > 1.0 else _as_float(getattr(candidate, "selected_implied_probability", None), 0.0)
    ev = (probability * odds - 1.0) * 100.0 if odds > 1.0 and probability > 0 else _as_float(getattr(candidate, "ev_pct", None), -999.0)
    edge = (probability - implied) * 100.0 if probability > 0 and implied > 0 else _as_float(getattr(candidate, "edge_pct", None), -999.0)
    return ev, edge


def _decision_for(candidate: Any, decisions: list[dict[str, Any]]) -> dict[str, Any]:
    match_key = str(getattr(candidate, "match_key", "") or "")
    selection_key = str(getattr(candidate, "selection_key", "") or "")
    family = str(getattr(candidate, "family", "") or "")
    point = getattr(candidate, "point", None)
    for row in decisions:
        if not isinstance(row, dict):
            continue
        if str(row.get("match_key") or "") != match_key:
            continue
        if selection_key and str(row.get("selection_key") or "") != selection_key:
            continue
        if family and str(row.get("family") or "") != family:
            continue
        if str(row.get("point") or "") != str(point or ""):
            # Do not require exact point when the row omitted it.
            if row.get("point") not in (None, "") or point not in (None, ""):
                continue
        return row
    return {}


def _eligible(candidate: Any, decision: dict[str, Any]) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    family = str(getattr(candidate, "family", "") or "")
    if family not in {"totals", "spreads"}:
        reasons.append("family_not_allowed")
    decision_reasons = [str(x) for x in list(decision.get("reasons") or [])]
    allowed_quality_reasons = {
        "post_calibration_probability_guard",
        "post_calibration_edge_guard",
        "post_calibration_ev_guard",
        "no_bet_quality_score_guard",
        "quality_post_calibration_probability_guard",
    }
    if decision_reasons and not any(reason in allowed_quality_reasons for reason in decision_reasons):
        reasons.append("quality_reason_not_relievable:" + ",".join(decision_reasons[:3]))

    min_odds_sources = max(2, _as_int(os.getenv("QUALITY_CONSENSUS_RELIEF_MIN_ODDS_SOURCES"), 2))
    min_context_sources = max(2, _as_int(os.getenv("QUALITY_CONSENSUS_RELIEF_MIN_CONTEXT_SOURCES"), 2))
    min_books = max(2, _as_int(os.getenv("QUALITY_CONSENSUS_RELIEF_MIN_BOOKS"), 2))
    if _odds_sources_count(candidate) < min_odds_sources:
        reasons.append("odds_sources_below_relief_min")
    if _context_sources_count(candidate) < min_context_sources:
        reasons.append("context_sources_below_relief_min")
    if _books_count(candidate) < min_books:
        reasons.append("books_below_relief_min")

    ev, edge = _canonical_ev_edge(candidate)
    min_ev = float(os.getenv("QUALITY_CONSENSUS_RELIEF_MIN_EV_PCT") or 0.0)
    min_edge = float(os.getenv("QUALITY_CONSENSUS_RELIEF_MIN_EDGE_PP") or 0.0)
    if ev < min_ev:
        reasons.append(f"ev_below_relief_min:{ev:.3f}")
    if edge < min_edge:
        reasons.append(f"edge_below_relief_min:{edge:.3f}")

    min_conf = float(os.getenv("QUALITY_CONSENSUS_RELIEF_MIN_CONFIDENCE") or 54.0)
    if _as_float(getattr(candidate, "confidence", None), 0.0) < min_conf:
        reasons.append("confidence_below_relief_min")

    # Do not rescue candidates explicitly marked as market-integrity failures.
    integrity_reasons = [str(x) for x in list(getattr(candidate, "integrity_reasons", []) or [])]
    if integrity_reasons:
        reasons.append("integrity_reasons_present:" + ",".join(integrity_reasons[:3]))

    return not reasons, reasons


def _rank(candidate: Any) -> tuple[float, float, float, int, int]:
    ev, edge = _canonical_ev_edge(candidate)
    return (
        ev,
        edge,
        _as_float(getattr(candidate, "confidence", None), 0.0),
        _odds_sources_count(candidate),
        _books_count(candidate),
    )


def _mark_relieved(candidate: Any, decision: dict[str, Any]) -> None:
    try:
        candidate.source_summary["quality_status"] = "passed_quality_consensus_relief"
        candidate.source_summary["quality_reasons"] = ["quality_consensus_safe_relief"]
        candidate.source_summary["quality_consensus_safe_relief"] = True
    except Exception:
        pass
    try:
        candidate.reasons.append("quality=quality_consensus_safe_relief")
    except Exception:
        pass
    try:
        candidate.diagnostics.setdefault("quality", {})
        candidate.diagnostics["quality"].update({
            "status": "passed_quality_consensus_relief",
            "original_status": decision.get("status"),
            "original_reasons": list(decision.get("reasons") or []),
            "relief": True,
        })
    except Exception:
        pass


def _patch_quality_service() -> dict[str, Any]:
    from app.services.quality import PredictionQualityService

    original = getattr(PredictionQualityService, "apply_to_candidates", None)
    if not callable(original):
        return {"quality_service": "missing_apply_to_candidates"}
    if getattr(original, "_harizon_quality_consensus_safe_relief", False):
        return {"quality_service": "already_patched"}

    def apply_to_candidates_with_consensus_relief(self: Any, candidates: list[Any], quality_report: dict[str, Any], now_utc: datetime):
        passed, rejections, debug = original(self, candidates, quality_report, now_utc)
        report: dict[str, Any] = {
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "input_candidates": len(candidates or []),
            "original_passed": len(passed or []),
            "enabled": _truthy(os.getenv("QUALITY_CONSENSUS_SAFE_RELIEF_ENABLED"), True),
            "rescued": 0,
            "sample": [],
        }
        if passed or not candidates or not report["enabled"]:
            _write(report)
            return passed, rejections, debug

        decisions = list((debug or {}).get("decisions") or []) if isinstance(debug, dict) else []
        eligible: list[Any] = []
        rejected_samples: list[dict[str, Any]] = []
        for candidate in list(candidates or []):
            decision = _decision_for(candidate, decisions)
            ok, reasons = _eligible(candidate, decision)
            ev, edge = _canonical_ev_edge(candidate)
            row = {
                "match_key": getattr(candidate, "match_key", ""),
                "home": getattr(candidate, "home_team", ""),
                "away": getattr(candidate, "away_team", ""),
                "family": getattr(candidate, "family", ""),
                "selection": getattr(candidate, "selection", ""),
                "point": getattr(candidate, "point", None),
                "ev_pct": round(ev, 4),
                "edge_pp": round(edge, 4),
                "confidence": round(_as_float(getattr(candidate, "confidence", None), 0.0), 3),
                "odds_sources": _odds_sources_count(candidate),
                "context_sources": _context_sources_count(candidate),
                "books": _books_count(candidate),
                "quality_reasons": list(decision.get("reasons") or []),
                "eligible": ok,
                "reject_reasons": reasons,
            }
            if ok:
                eligible.append(candidate)
            elif len(rejected_samples) < 20:
                rejected_samples.append(row)
            if len(report["sample"]) < 20:
                report["sample"].append(row)

        if eligible:
            eligible.sort(key=_rank, reverse=True)
            limit = max(1, _as_int(os.getenv("QUALITY_CONSENSUS_RELIEF_MAX_CANDIDATES"), 1))
            rescued = eligible[:limit]
            for candidate in rescued:
                _mark_relieved(candidate, _decision_for(candidate, decisions))
            passed = rescued
            rejections = dict(rejections or {})
            rejections["quality_consensus_safe_relief_used"] = len(rescued)
            if isinstance(debug, dict):
                debug["passed"] = len(passed)
                debug["rejected"] = max(0, len(candidates or []) - len(passed))
                debug["consensus_safe_relief"] = {
                    "rescued": len(rescued),
                    "candidates_considered": len(candidates or []),
                }
            report["rescued"] = len(rescued)
        report["rejected_samples"] = rejected_samples
        _write(report)
        return passed, rejections, debug

    apply_to_candidates_with_consensus_relief._harizon_quality_consensus_safe_relief = True  # type: ignore[attr-defined]
    PredictionQualityService.apply_to_candidates = apply_to_candidates_with_consensus_relief  # type: ignore[assignment]
    return {"quality_service": "patched"}


def install() -> dict[str, Any]:
    global _INSTALLED
    if _INSTALLED:
        return {"status": "already_installed"}
    _INSTALLED = True
    os.environ.setdefault("QUALITY_CONSENSUS_SAFE_RELIEF_ENABLED", "true")
    os.environ.setdefault("QUALITY_CONSENSUS_RELIEF_MIN_ODDS_SOURCES", "2")
    os.environ.setdefault("QUALITY_CONSENSUS_RELIEF_MIN_CONTEXT_SOURCES", "2")
    os.environ.setdefault("QUALITY_CONSENSUS_RELIEF_MIN_BOOKS", "2")
    os.environ.setdefault("QUALITY_CONSENSUS_RELIEF_MIN_EV_PCT", "0.0")
    os.environ.setdefault("QUALITY_CONSENSUS_RELIEF_MIN_EDGE_PP", "0.0")
    os.environ.setdefault("QUALITY_CONSENSUS_RELIEF_MIN_CONFIDENCE", "54.0")
    os.environ.setdefault("QUALITY_CONSENSUS_RELIEF_MAX_CANDIDATES", "1")
    payload = {"created_at_utc": datetime.now(timezone.utc).isoformat(), "status": "starting"}
    try:
        payload.update(_patch_quality_service())
        payload["status"] = "installed"
    except Exception as exc:
        payload["status"] = "error"
        payload["error"] = f"{type(exc).__name__}: {exc}"
    _write(payload)
    return payload
