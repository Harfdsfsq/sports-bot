"""Use Focused Alpha only as an ordering rule after all publication guards pass."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / ".data" / "exports" / "latest-focused-alpha-rank-patch.json"
_INSTALLED = False
_ORIGINAL = None
_HISTORY: dict[str, Any] | None = None


def _enabled() -> bool:
    return str(os.getenv("FOCUSED_ALPHA_ENABLED", "true")).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
        "force",
    }


def _write(payload: dict[str, Any]) -> None:
    try:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except Exception:
        pass


def _history() -> dict[str, Any]:
    global _HISTORY
    if _HISTORY is None:
        try:
            from app.services.focused_alpha_history import build_history_audit

            _HISTORY = build_history_audit()
        except Exception:
            _HISTORY = {"live_learning_ready": False, "by_league": {}}
    return _HISTORY


def _candidate_with_metrics(candidate: dict[str, Any], metrics: dict[str, Any]) -> dict[str, Any]:
    row = dict(candidate)
    row.update(
        {
            "odds": metrics.get("odds", candidate.get("odds")),
            "adjusted_probability": metrics.get(
                "adjusted_probability", candidate.get("adjusted_probability")
            ),
            "model_probability": metrics.get(
                "model_probability", candidate.get("model_probability")
            ),
            "market_probability": metrics.get(
                "market_probability", candidate.get("market_probability")
            ),
            "canonical_edge_pp": metrics.get("canonical_edge_pp"),
            "canonical_ev_pct": metrics.get("canonical_ev_pct"),
            "confidence": metrics.get("confidence", candidate.get("confidence")),
            "quality_score": metrics.get("quality_score"),
            "quality_score_source": metrics.get("quality_score_source"),
            "publication_score": metrics.get("publication_score"),
            "books_count": metrics.get("books_count"),
            "odds_sources_count": metrics.get("odds_sources_count"),
            "confirmation_sources_count": metrics.get("confirmation_sources_count"),
            "confirmation_sources": metrics.get("confirmation_sources"),
            "xg_sanity": metrics.get("xg_sanity"),
            "btts_sanity": metrics.get("btts_sanity"),
            "dnb_sanity": metrics.get("dnb_sanity"),
        }
    )
    return row


def install(base_module: Any) -> dict[str, Any]:
    global _INSTALLED, _ORIGINAL
    if _INSTALLED:
        return {"status": "already_installed"}
    current = getattr(base_module, "candidate_rank", None)
    if not callable(current):
        payload = {"status": "missing_candidate_rank", "publication_contract_relaxed": False}
        _write(payload)
        return payload
    if getattr(current, "_focused_alpha_candidate_rank", False):
        _INSTALLED = True
        return {"status": "already_patched"}
    _ORIGINAL = current

    def candidate_rank(
        candidate: dict[str, Any],
        metrics: dict[str, Any],
        tier: str,
    ) -> tuple[float, ...]:
        assert callable(_ORIGINAL)
        legacy = tuple(float(value) for value in _ORIGINAL(candidate, metrics, tier))
        if not _enabled():
            return legacy
        try:
            from scripts.build_focused_alpha_decisions import score_candidate

            scored = score_candidate(_candidate_with_metrics(candidate, metrics), _history())
            utility = float(scored.get("risk_adjusted_utility") or -999.0)
            conservative_ev = float(scored.get("conservative_ev_pct") or -999.0)
            # candidate_rank is reached only for candidates that have already passed
            # hard, tier and final guards. Focused Alpha changes their order only.
            return (utility, conservative_ev, *legacy)
        except Exception:
            return legacy

    candidate_rank._focused_alpha_candidate_rank = True
    base_module.candidate_rank = candidate_rank
    _INSTALLED = True
    payload = {
        "status": "installed",
        "created_at_utc": datetime.now(UTC).isoformat(),
        "policy": "accepted_candidates_ordered_by_conservative_utility_then_legacy_rank",
        "evaluates_rejected_candidates": False,
        "changes_publishability": False,
        "publication_contract_relaxed": False,
    }
    _write(payload)
    return payload


__all__ = ["install"]
