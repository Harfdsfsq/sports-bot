from __future__ import annotations

"""Recover the runtime funnel when lines/context exist but CandidateFactory returns raw=0.

The patch does not publish anything by itself. It only creates a conservative
pre-quality diagnostic candidate pool from already-fetched offers by allowing the
existing simple/market-derived builders to run with first-snapshot consensus
relief. Normal quality, publication, current-price, line-movement, xG and
Telegram guards still run afterwards.
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

OUT = Path(".data/exports/latest-zero-raw-candidate-recovery.json")
_INSTALLED = False


def _enabled(name: str, default: bool = True) -> bool:
    raw = os.getenv(name)
    if raw in (None, ""):
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on", "force"}


def _write(payload: dict[str, Any]) -> None:
    try:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except Exception:
        pass


def _set_temp(obj: Any, updates: dict[str, Any]) -> dict[str, Any]:
    old: dict[str, Any] = {}
    for key, value in updates.items():
        old[key] = getattr(obj, key, None)
        try:
            setattr(obj, key, value)
        except Exception:
            pass
    return old


def _restore(obj: Any, old: dict[str, Any]) -> None:
    for key, value in old.items():
        try:
            setattr(obj, key, value)
        except Exception:
            pass


def _offer_count(offers_by_match: Any) -> tuple[int, int]:
    if not isinstance(offers_by_match, dict):
        return 0, 0
    matches = 0
    offers = 0
    for rows in offers_by_match.values():
        if rows:
            matches += 1
            try:
                offers += len(rows)
            except Exception:
                offers += 1
    return matches, offers


def install(model_module: Any | None = None) -> dict[str, Any]:
    global _INSTALLED
    if _INSTALLED:
        return {"status": "already_installed"}
    if not _enabled("HARIZON_ZERO_RAW_CANDIDATE_RECOVERY_ENABLED", True):
        return {"status": "disabled"}
    if model_module is None:
        import app.services.model as model_module  # type: ignore[no-redef]

    cls = getattr(model_module, "CandidateFactory", None)
    if cls is None or getattr(cls, "_harizon_zero_raw_candidate_recovery", False):
        return {"status": "missing_or_already_patched"}

    original = cls.build_candidates

    def build_candidates(self: Any, matches: list[Any], offers_by_match: dict[str, list[Any]], contexts_by_match: dict[str, Any], market_signals_by_match: dict[str, dict[str, Any]] | None = None):
        candidates, rejections, debug = original(self, matches, offers_by_match, contexts_by_match, market_signals_by_match)
        matches_with_offers, total_offers = _offer_count(offers_by_match)
        payload: dict[str, Any] = {
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "status": "not_needed" if candidates else "zero_raw_seen",
            "initial_candidates": len(candidates or []),
            "matches": len(matches or []),
            "matches_with_offers": matches_with_offers,
            "offers": total_offers,
            "rejections": dict(rejections or {}),
            "recovered_candidates": 0,
            "normal_publication_guards_preserved": True,
        }
        if candidates or matches_with_offers <= 0:
            _write(payload)
            return candidates, rejections, debug

        # Existing builders were too dependent on line-history/market-signal state.
        # On a fresh run, a first snapshot can be a legitimate item for the later
        # lifecycle guard to carry over, so temporarily allow first-snapshot simple
        # candidates. Quality and publication layers still decide safety.
        temp = {
            "simple_market_fallback_enabled": True,
            "partial_context_market_fallback_enabled": True,
            "market_derived_candidates_enabled": True,
            "market_derived_allow_first_snapshot_candidates": True,
            "simple_market_min_signal_boost_pct": -0.01,
            "market_derived_min_edge_pct": 0.0,
            "market_derived_min_delta_prob_pp": -25.0,
            "market_derived_first_snapshot_min_edge_pct": 0.0,
            "market_derived_first_snapshot_max_dispersion_pct": 100.0,
            "market_derived_min_books": 1,
            "market_derived_min_sources": 1,
            "fallback_publish_mode_enabled": True,
            "model_relaxed_fallback_enabled": True,
        }
        old = _set_temp(self.settings, temp)
        try:
            recovered, rec_rejections, rec_debug = original(self, matches, offers_by_match, contexts_by_match, market_signals_by_match)
        finally:
            _restore(self.settings, old)

        for key, value in (rec_rejections or {}).items():
            rejections[f"zero_raw_recovery_{key}"] = rejections.get(f"zero_raw_recovery_{key}", 0) + int(value or 0)
        if recovered:
            for item in recovered:
                try:
                    item.reasons.append("zero_raw_candidate_recovery=first_snapshot_consensus")
                    item.source_summary["zero_raw_candidate_recovery"] = True
                    item.source_summary["zero_raw_recovery_mode"] = "first_snapshot_consensus"
                except Exception:
                    pass
            payload.update({
                "status": "recovered",
                "recovered_candidates": len(recovered),
                "recovery_rejections": dict(rec_rejections or {}),
                "note": "Recovered candidates are still passed through quality/publication guards.",
            })
            _write(payload)
            return recovered, rejections, rec_debug

        payload.update({"status": "not_recovered", "recovery_rejections": dict(rec_rejections or {})})
        _write(payload)
        return candidates, rejections, debug

    cls.build_candidates = build_candidates
    cls._harizon_zero_raw_candidate_recovery = True
    _INSTALLED = True
    _write({"created_at_utc": datetime.now(timezone.utc).isoformat(), "status": "installed", "normal_publication_guards_preserved": True})
    return {"status": "installed"}


if __name__ == "__main__":
    print(json.dumps(install(), ensure_ascii=False, indent=2))
