from __future__ import annotations

import os
from typing import Any

_PATCH_APPLIED = False


def _env_default(name: str, value: str) -> None:
    current = os.getenv(name)
    if current is None or str(current).strip() == "":
        os.environ[name] = str(value)


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        if isinstance(value, str):
            text = value.strip().replace(",", ".").replace("%", "")
            if not text:
                return default
            return float(text)
        return float(value)
    except Exception:
        return default


def _league_bucket(settings: Any, candidate: Any) -> str:
    league_name = str(getattr(candidate, "league_name", "") or "")
    summary = dict(getattr(candidate, "source_summary", {}) or {})
    tier = str(summary.get("match_tier") or "").strip().lower()
    if tier == "low":
        return "low"
    try:
        if getattr(settings, "is_preferred_league")(league_name):
            return "preferred"
    except Exception:
        pass
    try:
        if getattr(settings, "is_secondary_league")(league_name):
            return "secondary"
    except Exception:
        pass
    try:
        if getattr(settings, "is_low_tier_league")(league_name):
            return "low"
    except Exception:
        pass
    return "other"


def _shrink_pp(candidate: Any) -> float:
    summary = dict(getattr(candidate, "source_summary", {}) or {})
    raw = _to_float(summary.get("raw_model_probability"), _to_float(getattr(candidate, "model_probability", 0.0)))
    adj = _to_float(summary.get("adjusted_probability"), _to_float(getattr(candidate, "adjusted_probability", 0.0)))
    if raw <= 1.0 and adj <= 1.0:
        return abs(raw - adj) * 100.0
    return abs(raw - adj)


def _ensure_env_defaults() -> None:
    defaults = {
        # Risk / staking
        "BANKROLL_KELLY_FRACTION": "0.12",
        "BANKROLL_MIN_STAKE_PCT": "1.0",
        "BANKROLL_MAX_STAKE_PCT": "3.0",
        "BANKROLL_MAX_OPEN_EXPOSURE_PCT": "18.0",

        # Derived market gates
        "MARKET_DERIVED_MIN_BOOKS": "1",
        "MARKET_DERIVED_MIN_SOURCES": "1",
        "MARKET_DERIVED_MIN_EDGE_PCT": "0.55",
        "MARKET_DERIVED_MIN_DELTA_PROB_PP": "-0.20",
        "MARKET_DERIVED_MAX_DISPERSION_PCT": "12.5",
        "MARKET_DERIVED_CONSENSUS_RELIEF_ENABLED": "true",
        "MARKET_DERIVED_CONSENSUS_RELIEF_MIN_BOOKS": "1",
        "MARKET_DERIVED_CONSENSUS_RELIEF_MIN_SOURCES": "1",
        "MARKET_DERIVED_CONSENSUS_RELIEF_MIN_OBSERVATIONS": "1",
        "MARKET_DERIVED_CONSENSUS_RELIEF_MIN_EDGE_PCT": "0.55",
        "MARKET_DERIVED_CONSENSUS_RELIEF_MAX_DISPERSION_PCT": "12.5",
        "MARKET_DERIVED_CONSENSUS_RELIEF_PROBABILITY_BOOST_PCT": "1.35",

        # Totals coverage
        "SUPPORTED_TOTAL_LINES": "0.5,0.75,1.0,1.25,1.5,1.75,2.0,2.25,2.5,2.75,3.0,3.25,3.5,3.75,4.0,4.25,4.5,4.75,5.0,5.25,5.5,5.75,6.0",
        "LINE_SUPPORT_TOLERANCE": "0.15",
        "SIMPLE_MARKET_TOTALS_MIN_CONFIDENCE": "47",
        "SIMPLE_MARKET_TOTALS_MIN_EV_PCT": "0.45",
        "SIMPLE_MARKET_TOTALS_MIN_EDGE_PCT": "0.70",

        # Safer single-book publication relief for strong core signals
        "PREFERRED_SINGLE_BOOK_MIN_CONFIDENCE": "70",
        "PREFERRED_SINGLE_BOOK_MIN_EDGE_PCT": "5.0",
        "PREFERRED_SINGLE_BOOK_MIN_EV_PCT": "3.0",
        "PREFERRED_SINGLE_BOOK_MIN_PUBLICATION_SCORE": "14.0",
        "SECONDARY_SINGLE_BOOK_MIN_CONFIDENCE": "72",
        "SECONDARY_SINGLE_BOOK_MIN_EDGE_PCT": "5.5",
        "SECONDARY_SINGLE_BOOK_MIN_EV_PCT": "3.2",
        "SECONDARY_SINGLE_BOOK_MIN_PUBLICATION_SCORE": "15.0",
    }
    for key, value in defaults.items():
        _env_default(key, value)


def _patch_state_store() -> None:
    from app.state import JsonStateStore

    if getattr(JsonStateStore, "_stage_next_fix_applied", False):
        return

    original_stake_pct = JsonStateStore._stake_pct

    def _stake_pct(candidate: Any, settings: Any) -> float:
        base = float(original_stake_pct(candidate, settings))
        bucket = _league_bucket(settings, candidate)
        books = int(getattr(candidate, "books_count", 0) or 0)
        sources = int(getattr(candidate, "sources_count", 0) or 0)
        shrink = _shrink_pp(candidate)

        cap = float(getattr(settings, "bankroll_max_stake_pct", 3.0) or 3.0)
        if books <= 1 or sources <= 1:
            cap = min(cap, 2.2)
        if bucket in {"other", "low"}:
            cap = min(cap, 1.8)
        if (books <= 1 or sources <= 1) and shrink >= 12.0:
            cap = min(cap, 1.5)
        if bucket in {"other", "low"} and shrink >= 12.0:
            cap = min(cap, 1.2)

        min_pct = float(getattr(settings, "bankroll_min_stake_pct", 1.0) or 1.0)
        return max(min_pct, min(base, cap))

    JsonStateStore._stake_pct = staticmethod(_stake_pct)
    JsonStateStore._stage_next_fix_applied = True


def _patch_candidate_factory() -> None:
    from app.services.model import CandidateFactory

    if getattr(CandidateFactory, "_stage_next_fix_applied", False):
        return

    original_ready = CandidateFactory._market_signal_ready_for_derived
    original_required_publish_books = CandidateFactory._required_publish_books
    original_single_book_guard = CandidateFactory._passes_single_book_fallback_guard

    def _market_signal_ready_for_derived(self, family, market_signal, offers):
        ready = bool(original_ready(self, family, market_signal, offers))
        if ready:
            return True
        if not isinstance(market_signal, dict):
            return False

        books_count = int(market_signal.get("books_count") or 0)
        sources_count = int(market_signal.get("sources_count") or 0)
        if offers:
            try:
                books_count = max(
                    books_count,
                    len({self._norm_book(item.bookmaker) for item in offers if str(getattr(item, "bookmaker", "") or "").strip()})
                )
            except Exception:
                pass
            try:
                sources_count = max(
                    sources_count,
                    len({str(getattr(item, "source", "") or "").strip().lower() for item in offers if str(getattr(item, "source", "") or "").strip()})
                )
            except Exception:
                pass

        edge_pct = _to_float(market_signal.get("best_vs_consensus_edge_pct"), 0.0)
        delta_prob_pp = _to_float(market_signal.get("delta_prob_pp"), 0.0)
        dispersion_pct = _to_float(market_signal.get("consensus_dispersion_pct"), 999.0)
        history_ready = bool(market_signal.get("history_ready"))
        observation_count = int(market_signal.get("observation_count") or 0)
        selection_key = str(market_signal.get("selection_key") or "").strip().lower()

        if family == "h2h" and selection_key == "draw":
            return False

        if family == "totals":
            return books_count >= 1 and sources_count >= 1 and edge_pct >= 0.35 and dispersion_pct <= 14.5
        if family == "spreads":
            return books_count >= 1 and sources_count >= 1 and edge_pct >= 0.30 and dispersion_pct <= 14.5
        if family == "h2h":
            return (
                books_count >= 1
                and sources_count >= 1
                and edge_pct >= 0.55
                and dispersion_pct <= 13.0
                and (history_ready or observation_count >= 1 or delta_prob_pp >= -0.20)
            )
        return False

    def _passes_single_book_fallback_guard(self, item):
        if bool(original_single_book_guard(self, item)):
            return True

        books_count = int(getattr(item, "books_count", 0) or 0)
        if books_count >= 2:
            return True
        if books_count <= 0:
            return False

        bucket = self._league_bucket(item)
        if bucket not in {"preferred", "secondary"}:
            return False
        if not self._has_core_context(item):
            return False

        confidence = _to_float(getattr(item, "confidence", 0.0))
        edge_pct = _to_float(getattr(item, "edge_pct", 0.0))
        ev_pct = _to_float(getattr(item, "ev_pct", 0.0))
        pub = _to_float(getattr(item, "publication_score", 0.0))

        if bucket == "preferred":
            return confidence >= 70.0 and edge_pct >= 5.0 and ev_pct >= 3.0 and pub >= 14.0
        return confidence >= 72.0 and edge_pct >= 5.5 and ev_pct >= 3.2 and pub >= 15.0

    def _required_publish_books(self, item):
        books_count = int(getattr(item, "books_count", 0) or 0)
        if books_count >= 2:
            return 2
        if _passes_single_book_fallback_guard(self, item):
            return 1
        return original_required_publish_books(self, item)

    CandidateFactory._market_signal_ready_for_derived = _market_signal_ready_for_derived
    CandidateFactory._passes_single_book_fallback_guard = _passes_single_book_fallback_guard
    CandidateFactory._required_publish_books = _required_publish_books
    CandidateFactory._stage_next_fix_applied = True


def _apply() -> None:
    global _PATCH_APPLIED
    if _PATCH_APPLIED:
        return
    _ensure_env_defaults()
    _patch_state_store()
    _patch_candidate_factory()
    _PATCH_APPLIED = True


_apply()
