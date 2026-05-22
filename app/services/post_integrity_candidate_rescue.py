from __future__ import annotations

"""Post market-integrity candidate rescue.

This module is a discovery bridge, not a publisher.  It activates when the normal
CandidateFactory chain returns zero rows although offers/context exist.  In
hybrid Tier-B mode it may restore one-line-source candidates to the raw pool so
controlled fallback can evaluate the *final* contract: positive canonical value,
2+ books, 3+ context confirmations, xG sanity, quality stops and line movement.

It writes an artifact so zero-candidate runs are auditable instead of silently
showing "raw candidates = 0".
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.schemas import Offer

UTC = timezone.utc
ROOT = Path(__file__).resolve().parents[2]
REPORT_PATH = ROOT / ".data" / "exports" / "latest-post-integrity-candidate-rescue.json"


def _truthy(value: Any, default: bool = False) -> bool:
    raw = str(value if value is not None else "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on", "force"}


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(float(str(value)))
    except Exception:
        return default


def _float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(str(value).replace(",", "."))
    except Exception:
        return default


def _inc(rejections: dict[str, int], key: str, by: int = 1) -> None:
    try:
        rejections[key] = int(rejections.get(key) or 0) + by
    except Exception:
        pass


def _write(payload: dict[str, Any]) -> None:
    try:
        REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        REPORT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except Exception:
        pass


def _canonical_rank(candidate: Any) -> tuple[float, float, float, float]:
    odds = _float(getattr(candidate, "selected_odds", None), 0.0) or _float(getattr(candidate, "odds", None), 0.0)
    probability = (
        _float(getattr(candidate, "canonical_adjusted_probability", None), 0.0)
        or _float(getattr(candidate, "probability_used_for_ev", None), 0.0)
        or _float(getattr(candidate, "adjusted_probability", None), 0.0)
        or _float(getattr(candidate, "model_probability", None), 0.0)
    )
    ev = (probability * odds - 1.0) * 100.0 if odds > 1.0 and probability > 0 else _float(getattr(candidate, "ev_pct", None), -999.0)
    implied = 1.0 / odds if odds > 1.0 else 0.0
    edge = (probability - implied) * 100.0 if probability > 0 and implied > 0 else _float(getattr(candidate, "edge_pct", None), -999.0)
    return (ev, edge, _float(getattr(candidate, "confidence", None), 0.0), _float(getattr(candidate, "publication_score", None), 0.0))


def _sample(candidates: list[Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in candidates[:20]:
        out.append({
            "match_key": str(getattr(item, "match_key", "") or ""),
            "home": str(getattr(item, "home_team", "") or ""),
            "away": str(getattr(item, "away_team", "") or ""),
            "family": str(getattr(item, "family", "") or ""),
            "selection": str(getattr(item, "selection", "") or ""),
            "odds": round(_float(getattr(item, "odds", None), 0.0), 4),
            "books_count": _int(getattr(item, "books_count", 0), 0),
            "sources_count": _int(getattr(item, "sources_count", 0), 0),
            "rank": [round(x, 4) for x in _canonical_rank(item)],
        })
    return out


def install() -> dict[str, Any]:
    if not _truthy(os.getenv("POST_INTEGRITY_CANDIDATE_RESCUE_ENABLED"), True):
        result = {"status": "disabled"}
        _write({"created_at_utc": datetime.now(UTC).isoformat(), **result})
        return result
    try:
        from app.services import model
        from app.services import controlled_candidate_rescue
        from app.services import market_integrity
    except Exception as exc:
        result = {"status": "skipped", "reason": f"import_failed:{type(exc).__name__}:{exc}"}
        _write({"created_at_utc": datetime.now(UTC).isoformat(), **result})
        return result

    cls = getattr(model, "CandidateFactory", None)
    if cls is None:
        result = {"status": "skipped", "reason": "candidate_factory_missing"}
        _write({"created_at_utc": datetime.now(UTC).isoformat(), **result})
        return result
    if getattr(cls, "_harizon_post_integrity_candidate_rescue_patch", False):
        result = {"status": "already_installed"}
        _write({"created_at_utc": datetime.now(UTC).isoformat(), **result})
        return result
    original = getattr(cls, "build_candidates", None)
    build_rescue = getattr(controlled_candidate_rescue, "_build_rescue", None)
    if not callable(original) or not callable(build_rescue):
        result = {"status": "skipped", "reason": "missing_hooks"}
        _write({"created_at_utc": datetime.now(UTC).isoformat(), **result})
        return result

    def build_candidates_patched(
        self: Any,
        matches: list[Any],
        offers_by_match: dict[str, list[Offer]],
        contexts_by_match: dict[str, Any],
        market_signals_by_match: dict[str, dict[str, Any]] | None = None,
    ):
        candidates, rejections, debug = original(self, matches, offers_by_match, contexts_by_match, market_signals_by_match)
        if candidates or not offers_by_match:
            _write({
                "created_at_utc": datetime.now(UTC).isoformat(),
                "stage": "pass_through",
                "input_candidates": len(candidates or []),
                "offers_matches": len(offers_by_match or {}),
                "contexts_matches": len(contexts_by_match or {}),
            })
            return candidates, rejections, debug
        if not isinstance(rejections, dict):
            rejections = {}
        if not _truthy(os.getenv("POST_INTEGRITY_CANDIDATE_RESCUE_ENABLED"), True):
            return candidates, rejections, debug

        rescue_candidates, rescue_debug = build_rescue(self, matches, offers_by_match, contexts_by_match, rejections)
        if not rescue_candidates:
            _inc(rejections, "post_integrity_rescue_no_candidate")
            _write({
                "created_at_utc": datetime.now(UTC).isoformat(),
                "stage": "no_candidate",
                "offers_matches": len(offers_by_match or {}),
                "contexts_matches": len(contexts_by_match or {}),
                "rejection_keys": {k: v for k, v in sorted(rejections.items()) if "rescue" in str(k) or "market" in str(k)},
            })
            return candidates, rejections, debug

        before_integrity = len(rescue_candidates)
        hybrid_mode = _truthy(os.getenv("CONTROLLED_FALLBACK_SINGLE_LINE_CONTEXT_MODE_ENABLED"), True)
        apply_market_guard = _truthy(os.getenv("POST_INTEGRITY_RESCUE_APPLY_MARKET_GUARD"), True)
        if hybrid_mode and not _truthy(os.getenv("POST_INTEGRITY_RESCUE_APPLY_MARKET_GUARD_FOR_HYBRID"), False):
            apply_market_guard = False
            _inc(rejections, "post_integrity_rescue_market_guard_skipped_for_hybrid", 1)
        if apply_market_guard:
            rescue_candidates = market_integrity.filter_candidates(list(rescue_candidates), rejections)

        rescue_candidates.sort(key=_canonical_rank, reverse=True)
        limit = max(1, _int(os.getenv("POST_INTEGRITY_RESCUE_RETURN_LIMIT"), 24))
        returned = rescue_candidates[:limit]
        _inc(rejections, "post_integrity_rescue_built", before_integrity)
        _inc(rejections, "post_integrity_rescue_returned", len(returned))

        debug = dict(debug or {})
        debug["matches"] = (list(debug.get("matches") or []) + list(rescue_debug or []))[:240]
        debug["post_integrity_candidate_rescue"] = {
            "enabled": True,
            "built_before_market_integrity": before_integrity,
            "market_integrity_applied": bool(apply_market_guard),
            "hybrid_mode": bool(hybrid_mode),
            "returned": len(returned),
            "return_limit": limit,
            "sample": _sample(returned),
        }
        _write({
            "created_at_utc": datetime.now(UTC).isoformat(),
            "stage": "rescued",
            "built_before_market_integrity": before_integrity,
            "market_integrity_applied": bool(apply_market_guard),
            "hybrid_mode": bool(hybrid_mode),
            "returned": len(returned),
            "return_limit": limit,
            "sample": _sample(returned),
        })
        if returned:
            for candidate in returned:
                try:
                    reasons = list(getattr(candidate, "reasons", []) or [])
                    reasons.append("post_integrity_candidate_rescue:restored_after_hard_guard_zero_pool")
                    candidate.reasons = reasons
                    diagnostics = getattr(candidate, "diagnostics", None)
                    if isinstance(diagnostics, dict):
                        diagnostics["post_integrity_candidate_rescue"] = True
                except Exception:
                    pass
            return returned, rejections, debug
        return candidates, rejections, debug

    cls.build_candidates = build_candidates_patched
    cls._harizon_post_integrity_candidate_rescue_patch = True
    result = {"status": "installed", "version": "post-integrity-candidate-rescue-v3-audited-hybrid-bridge"}
    _write({"created_at_utc": datetime.now(UTC).isoformat(), **result})
    return result
