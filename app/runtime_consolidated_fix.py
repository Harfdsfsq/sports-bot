from __future__ import annotations

import os
from typing import Any

_PATCH_APPLIED = False


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


def _force_setting(obj: Any, name: str, value: Any) -> None:
    try:
        setattr(obj, name, value)
        return
    except Exception:
        pass
    try:
        object.__setattr__(obj, name, value)
    except Exception:
        return


def _league_bucket(candidate: Any) -> str:
    summary = dict(getattr(candidate, 'source_summary', {}) or {})
    tier = str(summary.get('match_tier') or '').strip().lower()
    if tier == 'low':
        return 'low'
    league = str(getattr(candidate, 'league_name', '') or '').lower()
    if any(x in league for x in ('premier league', 'championship', 'la liga', 'serie a', 'bundesliga', 'ligue 1')):
        return 'preferred'
    if any(x in league for x in ('league one', 'league two', 'eredivisie', 'super lig', 'segunda', 'serie b')):
        return 'secondary'
    return 'other'


def _shrink_pp(candidate: Any) -> float:
    summary = dict(getattr(candidate, 'source_summary', {}) or {})
    raw = _to_float(summary.get('raw_model_probability'), _to_float(getattr(candidate, 'model_probability', 0.0)))
    adj = _to_float(summary.get('adjusted_probability'), _to_float(getattr(candidate, 'adjusted_probability', 0.0)))
    if raw <= 1.0 and adj <= 1.0:
        return abs(raw - adj) * 100.0
    return abs(raw - adj)


def _patch_candidate_factory() -> None:
    from app.services.model import CandidateFactory

    if getattr(CandidateFactory, '_consolidated_fix_applied', False):
        return

    original_candidate_from_bucket = CandidateFactory._candidate_from_bucket
    original_ready = CandidateFactory._market_signal_ready_for_derived
    original_single_book_guard = CandidateFactory._passes_single_book_fallback_guard
    original_required_publish_books = CandidateFactory._required_publish_books

    def _candidate_from_bucket(self, *args, **kwargs):
        settings = getattr(self, 'settings', None)
        previous = None
        had_attr = False
        if settings is not None:
            try:
                previous = getattr(settings, 'min_sources_publish')
                had_attr = True
            except Exception:
                previous = None
            _force_setting(settings, 'min_sources_publish', 1)
        try:
            return original_candidate_from_bucket(self, *args, **kwargs)
        finally:
            if settings is not None and had_attr:
                _force_setting(settings, 'min_sources_publish', previous)

    def _market_signal_ready_for_derived(self, family, market_signal, offers):
        ready = bool(original_ready(self, family, market_signal, offers))
        if ready:
            return True
        if not isinstance(market_signal, dict):
            return False
        books_count = int(market_signal.get('books_count') or 0)
        sources_count = int(market_signal.get('sources_count') or 0)
        if offers:
            try:
                books_count = max(books_count, len({self._norm_book(item.bookmaker) for item in offers if str(getattr(item, 'bookmaker', '') or '').strip()}))
            except Exception:
                pass
            try:
                sources_count = max(sources_count, len({str(getattr(item, 'source', '') or '').strip().lower() for item in offers if str(getattr(item, 'source', '') or '').strip()}))
            except Exception:
                pass
        edge_pct = _to_float(market_signal.get('best_vs_consensus_edge_pct'), 0.0)
        delta_prob_pp = _to_float(market_signal.get('delta_prob_pp'), 0.0)
        dispersion_pct = _to_float(market_signal.get('consensus_dispersion_pct'), 999.0)
        history_ready = bool(market_signal.get('history_ready'))
        observation_count = int(market_signal.get('observation_count') or 0)
        selection_key = str(market_signal.get('selection_key') or '').strip().lower()
        if family == 'h2h' and selection_key == 'draw':
            return False
        if family == 'totals':
            return books_count >= 1 and sources_count >= 1 and edge_pct >= 0.35 and dispersion_pct <= 14.5
        if family == 'spreads':
            return books_count >= 1 and sources_count >= 1 and edge_pct >= 0.30 and dispersion_pct <= 14.5
        if family == 'h2h':
            return books_count >= 1 and sources_count >= 1 and edge_pct >= 0.55 and dispersion_pct <= 13.0 and (history_ready or observation_count >= 1 or delta_prob_pp >= -0.20)
        return False

    def _passes_single_book_fallback_guard(self, item):
        if bool(original_single_book_guard(self, item)):
            return True
        books_count = int(getattr(item, 'books_count', 0) or 0)
        if books_count >= 2:
            return True
        if books_count <= 0:
            return False
        if _league_bucket(item) not in {'preferred', 'secondary'}:
            return False
        if not self._has_core_context(item):
            return False
        confidence = _to_float(getattr(item, 'confidence', 0.0))
        edge_pct = _to_float(getattr(item, 'edge_pct', 0.0))
        ev_pct = _to_float(getattr(item, 'ev_pct', 0.0))
        pub = _to_float(getattr(item, 'publication_score', 0.0))
        if _league_bucket(item) == 'preferred':
            return confidence >= 70.0 and edge_pct >= 5.0 and ev_pct >= 3.0 and pub >= 14.0
        return confidence >= 72.0 and edge_pct >= 5.5 and ev_pct >= 3.2 and pub >= 15.0

    def _required_publish_books(self, item):
        books_count = int(getattr(item, 'books_count', 0) or 0)
        if books_count >= 2:
            return 2
        if _passes_single_book_fallback_guard(self, item):
            return 1
        return original_required_publish_books(self, item)

    CandidateFactory._candidate_from_bucket = _candidate_from_bucket
    CandidateFactory._market_signal_ready_for_derived = _market_signal_ready_for_derived
    CandidateFactory._passes_single_book_fallback_guard = _passes_single_book_fallback_guard
    CandidateFactory._required_publish_books = _required_publish_books
    CandidateFactory._consolidated_fix_applied = True


def _patch_state_store() -> None:
    from app.state import JsonStateStore
    if getattr(JsonStateStore, '_consolidated_fix_applied', False):
        return
    original_stake_pct = JsonStateStore._stake_pct

    def _stake_pct(candidate: Any, settings: Any) -> float:
        base = float(original_stake_pct(candidate, settings))
        bucket = _league_bucket(candidate)
        books = int(getattr(candidate, 'books_count', 0) or 0)
        sources = int(getattr(candidate, 'sources_count', 0) or 0)
        shrink = _shrink_pp(candidate)
        cap = min(3.0, _to_float(getattr(settings, 'bankroll_max_stake_pct', 3.0), 3.0))
        if books <= 1 or sources <= 1:
            cap = min(cap, 2.2)
        if bucket not in {'preferred', 'secondary'}:
            cap = min(cap, 1.8)
        if (books <= 1 or sources <= 1) and shrink >= 12.0:
            cap = min(cap, 1.5)
        if bucket not in {'preferred', 'secondary'} and shrink >= 12.0:
            cap = min(cap, 1.2)
        min_pct = max(0.5, _to_float(getattr(settings, 'bankroll_min_stake_pct', 1.0), 1.0))
        return max(min_pct, min(base, cap))

    JsonStateStore._stake_pct = staticmethod(_stake_pct)
    JsonStateStore._consolidated_fix_applied = True


def _patch_api_weather_percent_parsing() -> None:
    try:
        from app.providers import weather_common
        if getattr(weather_common, '_consolidated_fix_applied', False):
            return
        original = weather_common.WeatherContextEnricher._to_float
        def _to_float_safe(value: Any):
            try:
                if value in (None, ''):
                    return None
                if isinstance(value, str):
                    text = value.strip().replace(',', '.').replace('%', '')
                    if not text:
                        return None
                    return float(text)
                return float(value)
            except Exception:
                return None
        weather_common.WeatherContextEnricher._to_float = staticmethod(_to_float_safe)
        weather_common._consolidated_fix_applied = True
    except Exception:
        return


def _apply() -> None:
    global _PATCH_APPLIED
    if _PATCH_APPLIED:
        return
    os.environ['MIN_SOURCES_PUBLISH'] = '1'
    os.environ.setdefault('BANKROLL_KELLY_FRACTION', '0.12')
    os.environ.setdefault('BANKROLL_MAX_STAKE_PCT', '3.0')
    os.environ.setdefault('BANKROLL_MAX_OPEN_EXPOSURE_PCT', '18.0')
    _patch_candidate_factory()
    _patch_state_store()
    _patch_api_weather_percent_parsing()
    _PATCH_APPLIED = True


_apply()
