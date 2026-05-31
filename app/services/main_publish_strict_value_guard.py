from __future__ import annotations

"""Final strict-value guard for main pipeline publications.

The main pipeline can restore candidates through controlled-consensus/post-integrity
rescue paths. Those candidates are useful for keeping the model pool alive, but
publication must not be softer than the controlled fallback contract, especially
for non-core leagues.  This patch runs at the last publication boundary
(`PredictionRunner._filter_publishable_candidates`) and blocks weak rescue picks
that passed coverage but do not have enough final EV/edge/quality.

It does not touch controlled fallback scripts. It only prevents the main pipeline
from sending low-margin rescue picks as "best bets".
"""

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
REPORT_PATH = ROOT / ".data" / "exports" / "latest-main-publish-strict-value-guard.json"
_MARKER = "_harizon_main_publish_strict_value_guard_v1"
UTC = timezone.utc


def _truthy(value: Any, default: bool = False) -> bool:
    if value is None or str(value).strip() == "":
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on", "force"}


def _float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(str(value).replace(",", "."))
    except Exception:
        return default


def _env_float(name: str, default: float) -> float:
    return _float(os.getenv(name), default)


def _write_report(payload: dict[str, Any]) -> None:
    try:
        REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        REPORT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except Exception:
        pass


def _dict(obj: Any, name: str) -> dict[str, Any]:
    value = getattr(obj, name, None)
    return value if isinstance(value, dict) else {}


def _nested_get(container: dict[str, Any], *path: str) -> Any:
    cur: Any = container
    for key in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


def _metric(candidate: Any, name: str, *aliases: str, default: float = 0.0) -> float:
    for key in (name, *aliases):
        if hasattr(candidate, key):
            value = getattr(candidate, key, None)
            if value not in (None, ""):
                return _float(value, default)
    for container_name in ("source_summary", "diagnostics", "analysis"):
        container = _dict(candidate, container_name)
        for key in (name, *aliases):
            if key in container:
                return _float(container.get(key), default)
        # Common nested quality layout.
        quality = container.get("quality")
        if isinstance(quality, dict):
            for key in (name, *aliases):
                if key in quality:
                    return _float(quality.get(key), default)
    return default


def _quality_score(candidate: Any) -> float:
    direct = _metric(candidate, "quality_score", default=-1.0)
    if direct >= 0:
        return direct
    for container_name in ("source_summary", "diagnostics"):
        container = _dict(candidate, container_name)
        for key in ("quality_score", "q", "model_quality"):
            if key in container:
                return _float(container.get(key), 0.0)
        q = _nested_get(container, "quality", "quality_score")
        if q is not None:
            return _float(q, 0.0)
        q = _nested_get(container, "quality", "score")
        if q is not None:
            return _float(q, 0.0)
    return 0.0


def _is_rescue_candidate(candidate: Any) -> bool:
    mode = str(getattr(candidate, "model_mode", "") or "").lower()
    if "rescue" in mode or "controlled_consensus" in mode:
        return True
    for container_name in ("source_summary", "diagnostics", "analysis"):
        container = _dict(candidate, container_name)
        for key in (
            "controlled_consensus_rescue",
            "controlled_prefilter_rescue",
            "post_integrity_candidate_rescue",
            "post_integrity_rescue",
        ):
            if _truthy(container.get(key), False):
                return True
    reasons = " ".join(str(x).lower() for x in (getattr(candidate, "reasons", None) or []))
    return "rescue" in reasons or "controlled_prefilter" in reasons or "post_integrity" in reasons


_NON_CORE_PATTERNS = [
    r"\bserie\s*d\b",
    r"\bserie\s*c\b",
    r"\bbrasileiro\s+serie\s+d\b",
    r"\breserve\b",
    r"\breserves\b",
    r"\bu\s*\d{2}\b",
    r"\bu[- ]?\d{2}\b",
    r"\byouth\b",
    r"\bwomen\b",
    r"\bamateur\b",
    r"\bregional\b",
    r"\bfriendly\b",
    r"\blower\b",
]


def _is_non_core(candidate: Any) -> bool:
    summary = _dict(candidate, "source_summary")
    risk = str(summary.get("risk_label") or summary.get("match_tier") or getattr(candidate, "risk_label", "") or "").lower()
    if any(token in risk for token in ("non-core", "non_core", "low", "other")):
        return True
    league = str(getattr(candidate, "league_name", "") or "").lower()
    return any(re.search(pattern, league) for pattern in _NON_CORE_PATTERNS)


def strict_reject_reasons(candidate: Any) -> list[str]:
    """Return publication-blocking reasons for weak main-pipeline rescue picks."""
    if not _truthy(os.getenv("MAIN_PUBLISH_STRICT_VALUE_GUARD_ENABLED"), True):
        return []
    if not _is_rescue_candidate(candidate):
        return []

    non_core = _is_non_core(candidate)
    prefix = "main_publish_non_core_rescue" if non_core else "main_publish_rescue"
    min_ev = _env_float(
        "MAIN_PUBLISH_NON_CORE_RESCUE_MIN_EV_PCT" if non_core else "MAIN_PUBLISH_RESCUE_MIN_EV_PCT",
        3.5 if non_core else 2.6,
    )
    min_edge = _env_float(
        "MAIN_PUBLISH_NON_CORE_RESCUE_MIN_EDGE_PP" if non_core else "MAIN_PUBLISH_RESCUE_MIN_EDGE_PP",
        3.0 if non_core else 2.2,
    )
    min_quality = _env_float(
        "MAIN_PUBLISH_NON_CORE_RESCUE_MIN_QUALITY" if non_core else "MAIN_PUBLISH_RESCUE_MIN_QUALITY",
        70.0 if non_core else 66.0,
    )
    min_conf = _env_float(
        "MAIN_PUBLISH_NON_CORE_RESCUE_MIN_CONFIDENCE" if non_core else "MAIN_PUBLISH_RESCUE_MIN_CONFIDENCE",
        76.0 if non_core else 72.0,
    )

    ev = _metric(candidate, "ev_pct", default=0.0)
    edge = _metric(candidate, "edge_pct", "edge_pp", default=0.0)
    confidence = _metric(candidate, "confidence", default=0.0)
    quality = _quality_score(candidate)

    reasons: list[str] = []
    if ev < min_ev:
        reasons.append(f"{prefix}_ev_below_min:{ev:.2f}/{min_ev:.2f}")
    if edge < min_edge:
        reasons.append(f"{prefix}_edge_below_min:{edge:.2f}/{min_edge:.2f}")
    if confidence < min_conf:
        reasons.append(f"{prefix}_confidence_below_min:{confidence:.2f}/{min_conf:.2f}")
    if quality < min_quality:
        reasons.append(f"{prefix}_quality_below_min:{quality:.2f}/{min_quality:.2f}")
    return reasons


def _candidate_label(candidate: Any) -> dict[str, Any]:
    return {
        "match_key": str(getattr(candidate, "match_key", "") or ""),
        "home_team": str(getattr(candidate, "home_team", "") or ""),
        "away_team": str(getattr(candidate, "away_team", "") or ""),
        "league_name": str(getattr(candidate, "league_name", "") or ""),
        "selection": str(getattr(candidate, "selection", "") or ""),
        "odds": _metric(candidate, "odds", default=0.0),
        "ev_pct": _metric(candidate, "ev_pct", default=0.0),
        "edge_pct": _metric(candidate, "edge_pct", "edge_pp", default=0.0),
        "confidence": _metric(candidate, "confidence", default=0.0),
        "quality_score": _quality_score(candidate),
        "model_mode": str(getattr(candidate, "model_mode", "") or ""),
        "is_non_core": _is_non_core(candidate),
        "is_rescue": _is_rescue_candidate(candidate),
    }


def install() -> dict[str, Any]:
    if not _truthy(os.getenv("MAIN_PUBLISH_STRICT_VALUE_GUARD_ENABLED"), True):
        return {"status": "disabled"}
    try:
        from app.services.runner import PredictionRunner
    except Exception as exc:
        return {"status": "error", "error": f"import_runner:{type(exc).__name__}: {exc}"}

    original = getattr(PredictionRunner, "_filter_publishable_candidates", None)
    if not callable(original):
        return {"status": "missing_filter_publishable_candidates"}
    if getattr(original, _MARKER, False):
        return {"status": "already_installed"}

    def filter_publishable_candidates_strict(self: Any, candidates: list[Any]):  # type: ignore[no-untyped-def]
        publishable = list(original(self, candidates) or [])
        kept: list[Any] = []
        blocked: list[dict[str, Any]] = []
        for candidate in publishable:
            reasons = strict_reject_reasons(candidate)
            if reasons:
                try:
                    candidate.reasons = list(getattr(candidate, "reasons", []) or []) + reasons
                    summary = getattr(candidate, "source_summary", None)
                    if isinstance(summary, dict):
                        summary["main_publish_strict_value_guard"] = {"blocked": True, "reasons": reasons}
                    diagnostics = getattr(candidate, "diagnostics", None)
                    if isinstance(diagnostics, dict):
                        diagnostics["main_publish_strict_value_guard"] = {"blocked": True, "reasons": reasons}
                except Exception:
                    pass
                blocked.append({**_candidate_label(candidate), "reasons": reasons})
                continue
            kept.append(candidate)
        _write_report({
            "status": "ok",
            "updated_at_utc": datetime.now(UTC).isoformat(),
            "input_publishable": len(publishable),
            "kept": len(kept),
            "blocked": len(blocked),
            "blocked_candidates": blocked[:20],
            "notes": [
                "Blocks weak rescue/controlled-consensus main-pipeline picks after coverage passed.",
                "Fallback publication remains controlled by controlled_fallback_prepublish_guard.",
            ],
        })
        return kept

    setattr(filter_publishable_candidates_strict, _MARKER, True)
    setattr(filter_publishable_candidates_strict, "_harizon_original", original)
    PredictionRunner._filter_publishable_candidates = filter_publishable_candidates_strict  # type: ignore[assignment]
    return {"status": "installed", "version": "main-publish-strict-value-guard-v1"}
