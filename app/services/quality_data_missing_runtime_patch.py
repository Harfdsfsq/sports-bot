from __future__ import annotations

"""Prevent missing quality evidence from being reported as a real score of 0.0.

A zero score can be a valid numeric result, but in the HARIZON runtime it also
appears when raw quality inputs are absent. The latter must be visible as a data
problem and must never become an accidental publication path.
"""

from datetime import datetime
from typing import Any

MARKER = "_harizon_quality_data_missing_guard_v1"


def _dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def quality_data_missing(candidate: Any) -> bool:
    summary = _dict(getattr(candidate, "source_summary", None))
    diagnostics = _dict(getattr(candidate, "diagnostics", None))
    quality = _dict(diagnostics.get("quality"))
    source = str(
        summary.get("quality_score_source")
        or quality.get("quality_score_source")
        or quality.get("source")
        or ""
    ).strip().lower()
    explicit_missing = any(
        bool(container.get(key))
        for container in (summary, quality, diagnostics)
        for key in ("quality_data_missing", "quality_sources_raw_missing", "raw_missing")
    )
    if explicit_missing or source in {"missing", "raw_missing", "quality_data_missing", "none"}:
        return True
    score = summary.get("quality_score")
    if score is None:
        score = quality.get("quality_score")
    if score in (None, "", 0, 0.0, "0", "0.0"):
        # Do not call an explicit non-empty quality source missing merely because
        # its numeric score is zero; only the absent-input case belongs here.
        source_names = summary.get("quality_sources") or quality.get("quality_sources")
        return not bool(source_names)
    return False


def mark_quality_data_missing(candidate: Any) -> None:
    summary = _dict(getattr(candidate, "source_summary", None))
    summary["quality_status"] = "quality_data_missing"
    summary["quality_score"] = None
    summary["quality_score_source"] = "raw_missing"
    reasons = list(summary.get("quality_reasons") or [])
    if "quality_data_missing" not in reasons:
        reasons.append("quality_data_missing")
    summary["quality_reasons"] = reasons
    candidate.source_summary = summary

    diagnostics = _dict(getattr(candidate, "diagnostics", None))
    quality = _dict(diagnostics.get("quality"))
    quality["status"] = "quality_data_missing"
    quality["quality_score"] = None
    quality["quality_score_source"] = "raw_missing"
    quality["reasons"] = list(dict.fromkeys(list(quality.get("reasons") or []) + ["quality_data_missing"]))
    quality["marked_at"] = datetime.utcnow().isoformat() + "Z"
    diagnostics["quality"] = quality
    candidate.diagnostics = diagnostics
    reasons = list(getattr(candidate, "reasons", []) or [])
    if "quality=quality_data_missing" not in reasons:
        reasons.append("quality=quality_data_missing")
    candidate.reasons = reasons


def install() -> dict[str, Any]:
    try:
        from app.services.quality import PredictionQualityService
    except Exception as exc:
        return {"status": "import_error", "error": f"{type(exc).__name__}: {exc}"}
    current = getattr(PredictionQualityService, "apply_to_candidates", None)
    if not callable(current):
        return {"status": "method_missing"}
    if getattr(current, MARKER, False):
        return {"status": "already_installed"}

    def wrapped(self: Any, candidates: list[Any], quality_report: dict[str, Any], now_utc: Any):
        passed, rejections, debug = current(self, candidates, quality_report, now_utc)
        missing = [candidate for candidate in candidates if quality_data_missing(candidate)]
        if not missing:
            return passed, rejections, debug
        missing_ids = {id(candidate) for candidate in missing}
        passed = [candidate for candidate in passed if id(candidate) not in missing_ids]
        rejections = dict(rejections or {})
        rejections["quality_data_missing"] = int(rejections.get("quality_data_missing", 0) or 0) + len(missing)
        for candidate in missing:
            mark_quality_data_missing(candidate)
        debug = dict(debug or {})
        debug["quality_data_missing"] = {
            "count": len(missing),
            "match_keys": [str(getattr(candidate, "match_key", "") or "") for candidate in missing],
            "status": "excluded_missing_quality_data",
        }
        return passed, rejections, debug

    setattr(wrapped, MARKER, True)
    PredictionQualityService.apply_to_candidates = wrapped
    return {"status": "installed"}
