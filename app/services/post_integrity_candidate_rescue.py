from __future__ import annotations

"""Post market-integrity candidate rescue.

The normal CandidateFactory may build useful pre-filter rows, but the hard
market-integrity wrapper can reduce the final raw pool to zero. This module is
installed after market_integrity and only activates in that exact situation:

* offers_by_match exists;
* the wrapped factory returned no candidates;
* controlled consensus rescue can rebuild paired-book totals/DNB/BTTS candidates;
* market_integrity validates those rebuilt candidates.

It does not publish anything directly. It only restores a raw candidate pool for
quality/fallback/line-movement guards.
"""

import os
from typing import Any

from app.schemas import Offer


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


def _inc(rejections: dict[str, int], key: str, by: int = 1) -> None:
    try:
        rejections[key] = int(rejections.get(key) or 0) + by
    except Exception:
        pass


def install() -> dict[str, Any]:
    if not _truthy(os.getenv("POST_INTEGRITY_CANDIDATE_RESCUE_ENABLED"), True):
        return {"status": "disabled"}
    try:
        from app.services import model
        from app.services import controlled_candidate_rescue
        from app.services import market_integrity
    except Exception as exc:
        return {"status": "skipped", "reason": f"import_failed:{type(exc).__name__}:{exc}"}

    cls = getattr(model, "CandidateFactory", None)
    if cls is None:
        return {"status": "skipped", "reason": "candidate_factory_missing"}
    if getattr(cls, "_harizon_post_integrity_candidate_rescue_patch", False):
        return {"status": "already_installed"}
    original = getattr(cls, "build_candidates", None)
    build_rescue = getattr(controlled_candidate_rescue, "_build_rescue", None)
    if not callable(original) or not callable(build_rescue):
        return {"status": "skipped", "reason": "missing_hooks"}

    def build_candidates_patched(
        self: Any,
        matches: list[Any],
        offers_by_match: dict[str, list[Offer]],
        contexts_by_match: dict[str, Any],
        market_signals_by_match: dict[str, dict[str, Any]] | None = None,
    ):
        candidates, rejections, debug = original(self, matches, offers_by_match, contexts_by_match, market_signals_by_match)
        if candidates or not offers_by_match:
            return candidates, rejections, debug
        if not isinstance(rejections, dict):
            rejections = {}
        if not _truthy(os.getenv("POST_INTEGRITY_CANDIDATE_RESCUE_ENABLED"), True):
            return candidates, rejections, debug

        rescue_candidates, rescue_debug = build_rescue(self, matches, offers_by_match, contexts_by_match, rejections)
        if not rescue_candidates:
            _inc(rejections, "post_integrity_rescue_no_candidate")
            return candidates, rejections, debug

        before_integrity = len(rescue_candidates)
        if _truthy(os.getenv("POST_INTEGRITY_RESCUE_APPLY_MARKET_GUARD"), True):
            rescue_candidates = market_integrity.filter_candidates(list(rescue_candidates), rejections)
        rescue_candidates.sort(
            key=lambda item: (
                float(getattr(item, "publication_score", 0.0) or 0.0),
                float(getattr(item, "ev_pct", 0.0) or 0.0),
                float(getattr(item, "confidence", 0.0) or 0.0),
            ),
            reverse=True,
        )
        limit = max(1, _int(os.getenv("POST_INTEGRITY_RESCUE_RETURN_LIMIT"), 24))
        returned = rescue_candidates[:limit]
        _inc(rejections, "post_integrity_rescue_built", before_integrity)
        _inc(rejections, "post_integrity_rescue_after_market_integrity", len(returned))

        debug = dict(debug or {})
        debug["matches"] = (list(debug.get("matches") or []) + list(rescue_debug or []))[:240]
        debug["post_integrity_candidate_rescue"] = {
            "enabled": True,
            "built_before_market_integrity": before_integrity,
            "returned_after_market_integrity": len(returned),
            "return_limit": limit,
        }
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
    return {"status": "installed", "version": "post-integrity-candidate-rescue-v1"}
