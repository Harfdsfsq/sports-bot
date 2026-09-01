from __future__ import annotations

"""Recover the runtime funnel when live lines exist but CandidateFactory returns 0.

This patch is intentionally conservative: it only creates a pre-quality pool from
already fetched offers. It does not publish by itself; quality, stake, current
price, line movement, duplicate and controlled-fallback guards still run later.

The 2026-08-16 00:09 run had matches_with_offers=7 but candidates_before_quality=0.
The previous recovery only relaxed model thresholds, but still kept target
bookmaker/source gates. If odds-api returns useful prices from non-target books,
the factory can silently end at 0. In recovery mode we temporarily allow all
fetched bookmakers and lower source/book minimums to 1 so later safety layers can
see/evaluate the best available setups instead of having no pool at all.
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


def _offer_count(offers_by_match: Any) -> tuple[int, int, int]:
    if not isinstance(offers_by_match, dict):
        return 0, 0, 0
    matches = 0
    offers = 0
    books: set[str] = set()
    for rows in offers_by_match.values():
        if rows:
            matches += 1
            try:
                offers += len(rows)
            except Exception:
                offers += 1
            for row in rows or []:
                book = str(getattr(row, 'bookmaker', '') or '').strip()
                if book:
                    books.add(book.lower())
    return matches, offers, len(books)


def _mark_recovered(items: list[Any], mode: str) -> None:
    for item in items or []:
        try:
            item.reasons.append(f"zero_raw_candidate_recovery={mode}")
            item.source_summary["zero_raw_candidate_recovery"] = True
            item.source_summary["zero_raw_recovery_mode"] = mode
            item.source_summary["publication_contract_relaxed"] = False
        except Exception:
            pass


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
        matches_with_offers, total_offers, unique_books = _offer_count(offers_by_match)
        payload: dict[str, Any] = {
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "status": "not_needed" if candidates else "zero_raw_seen",
            "initial_candidates": len(candidates or []),
            "matches": len(matches or []),
            "matches_with_offers": matches_with_offers,
            "offers": total_offers,
            "unique_books": unique_books,
            "rejections": dict(rejections or {}),
            "recovered_candidates": 0,
            "normal_publication_guards_preserved": True,
        }
        if candidates:
            _write(payload)
            return candidates, rejections, debug
        if matches_with_offers <= 0:
            payload["status"] = "zero_raw_no_offer_input"
            _write(payload)
            return candidates, rejections, debug

        # Recovery pass 1: first-snapshot/market-derived relief, but keep the
        # existing target-bookmaker set. This handles missing market history.
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
            "force_publish_when_empty_enabled": False,
            "min_sources_publish": 1,
        }
        old = _set_temp(self.settings, temp)
        try:
            recovered, rec_rejections, rec_debug = original(self, matches, offers_by_match, contexts_by_match, market_signals_by_match)
        finally:
            _restore(self.settings, old)

        # Recovery pass 2: if target/consensus bookmaker filters caused the zero
        # pool, temporarily allow every fetched book. Publication still later
        # requires current price/integrity/quorum checks, so this only exposes
        # candidates for diagnostics and controlled fallback evaluation.
        if not recovered:
            old_settings = _set_temp(self.settings, temp)
            old_factory = {
                "target_books": getattr(self, "target_books", set()),
                "consensus_books": getattr(self, "consensus_books", set()),
            }
            try:
                self.target_books = set()
                self.consensus_books = set()
                recovered, rec2_rejections, rec_debug = original(self, matches, offers_by_match, contexts_by_match, market_signals_by_match)
                for key, value in (rec2_rejections or {}).items():
                    rec_rejections[f"all_books_{key}"] = rec_rejections.get(f"all_books_{key}", 0) + int(value or 0)
            finally:
                self.target_books = old_factory["target_books"]
                self.consensus_books = old_factory["consensus_books"]
                _restore(self.settings, old_settings)

        for key, value in (rec_rejections or {}).items():
            try:
                rejections[f"zero_raw_recovery_{key}"] = rejections.get(f"zero_raw_recovery_{key}", 0) + int(value or 0)
            except Exception:
                pass
        if recovered:
            mode = "first_snapshot_all_books" if any(str(k).startswith("all_books_") for k in (rec_rejections or {}).keys()) else "first_snapshot_consensus"
            _mark_recovered(recovered, mode)
            payload.update({
                "status": "recovered",
                "recovered_candidates": len(recovered),
                "recovery_mode": mode,
                "recovery_rejections": dict(rec_rejections or {}),
                "note": "Recovered candidates are still passed through quality/publication/current-price guards.",
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
