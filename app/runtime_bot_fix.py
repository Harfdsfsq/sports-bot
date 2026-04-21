from __future__ import annotations

import os
from typing import Any


def _force_env(name: str, value: str) -> None:
    os.environ[name] = value


def _apply_env_overrides() -> None:
    # Retry empty caches sooner so the bot can accumulate coverage during the day
    # instead of freezing empty API responses for multiple runs.
    _force_env('DAILY_COVERAGE_EMPTY_MATCHES_TTL_MINUTES', os.getenv('DAILY_COVERAGE_EMPTY_MATCHES_TTL_MINUTES') or '20')
    _force_env('DAILY_COVERAGE_EMPTY_OFFERS_TTL_MINUTES', os.getenv('DAILY_COVERAGE_EMPTY_OFFERS_TTL_MINUTES') or '25')
    _force_env('DAILY_COVERAGE_EMPTY_CONTEXT_TTL_MINUTES', os.getenv('DAILY_COVERAGE_EMPTY_CONTEXT_TTL_MINUTES') or '45')
    _force_env('DAILY_COVERAGE_CONTEXT_TTL_MINUTES', os.getenv('DAILY_COVERAGE_CONTEXT_TTL_MINUTES') or '240')
    _force_env('DAILY_COVERAGE_PREMIUM_CONTEXT_TTL_MINUTES', os.getenv('DAILY_COVERAGE_PREMIUM_CONTEXT_TTL_MINUTES') or '180')
    _force_env('DAILY_COVERAGE_NEWS_CONTEXT_TTL_MINUTES', os.getenv('DAILY_COVERAGE_NEWS_CONTEXT_TTL_MINUTES') or '90')

    # Make market-derived fallback less brittle when only consensus+movement exist.
    _force_env('MARKET_DERIVED_MAX_DISPERSION_PCT', os.getenv('MARKET_DERIVED_MAX_DISPERSION_PCT') or '10.0')
    _force_env('MARKET_DERIVED_CONSENSUS_RELIEF_MIN_EDGE_PCT', os.getenv('MARKET_DERIVED_CONSENSUS_RELIEF_MIN_EDGE_PCT') or '0.9')
    _force_env('MARKET_DERIVED_CONSENSUS_RELIEF_MAX_DISPERSION_PCT', os.getenv('MARKET_DERIVED_CONSENSUS_RELIEF_MAX_DISPERSION_PCT') or '10.0')
    _force_env('MARKET_DERIVED_CONSENSUS_RELIEF_PROBABILITY_BOOST_PCT', os.getenv('MARKET_DERIVED_CONSENSUS_RELIEF_PROBABILITY_BOOST_PCT') or '1.10')
    _force_env('SIMPLE_MARKET_MIN_SIGNAL_BOOST_PCT', os.getenv('SIMPLE_MARKET_MIN_SIGNAL_BOOST_PCT') or '0.08')
    _force_env('SIMPLE_MARKET_TOTALS_MIN_CONFIDENCE', os.getenv('SIMPLE_MARKET_TOTALS_MIN_CONFIDENCE') or '49')
    _force_env('SIMPLE_MARKET_TOTALS_MIN_EV_PCT', os.getenv('SIMPLE_MARKET_TOTALS_MIN_EV_PCT') or '0.6')
    _force_env('SIMPLE_MARKET_TOTALS_MIN_EDGE_PCT', os.getenv('SIMPLE_MARKET_TOTALS_MIN_EDGE_PCT') or '0.9')
    _force_env('SIMPLE_MARKET_SPREADS_MIN_CONFIDENCE', os.getenv('SIMPLE_MARKET_SPREADS_MIN_CONFIDENCE') or '50')
    _force_env('SIMPLE_MARKET_SPREADS_MIN_EV_PCT', os.getenv('SIMPLE_MARKET_SPREADS_MIN_EV_PCT') or '0.7')
    _force_env('SIMPLE_MARKET_SPREADS_MIN_EDGE_PCT', os.getenv('SIMPLE_MARKET_SPREADS_MIN_EDGE_PCT') or '1.0')
    _force_env('SIMPLE_MARKET_H2H_HIGH_ODDS_SKIP_MAX', os.getenv('SIMPLE_MARKET_H2H_HIGH_ODDS_SKIP_MAX') or '3.75')


_apply_env_overrides()


def _patch_api_football() -> None:
    try:
        from app.providers.api_football import ApiFootballContextProvider
    except Exception:
        return

    original_to_float = getattr(ApiFootballContextProvider, '_to_float', None)
    if not callable(original_to_float):
        return

    @staticmethod
    def _patched_to_float(value: Any) -> float | None:
        try:
            if value in (None, ''):
                return None
            text = str(value).strip().replace('%', '').replace(',', '.')
            return float(text)
        except Exception:
            return None

    ApiFootballContextProvider._to_float = _patched_to_float  # type: ignore[assignment]


def _patch_candidate_factory() -> None:
    try:
        from app.services.model import CandidateFactory, russian_selection
        from app.utils import clamp
    except Exception:
        return

    original_signal_ready = getattr(CandidateFactory, '_market_signal_ready_for_derived', None)
    original_simple_market_prob = getattr(CandidateFactory, '_simple_market_model_probability', None)
    original_build_simple_h2h = getattr(CandidateFactory, '_build_simple_market_h2h_candidates', None)
    if not callable(original_signal_ready) or not callable(original_simple_market_prob) or not callable(original_build_simple_h2h):
        return

    def _patched_market_signal_ready_for_derived(self, family, market_signal, offers):
        if original_signal_ready(self, family, market_signal, offers):
            return True
        if not isinstance(market_signal, dict):
            return False
        if family not in {'totals', 'spreads', 'h2h'}:
            return False
        books_count = int(market_signal.get('books_count') or 0)
        sources_count = int(market_signal.get('sources_count') or 0)
        if offers is not None:
            books_count = max(books_count, len({self._norm_book(item.bookmaker) for item in offers if str(item.bookmaker or '').strip()}))
            sources_count = max(sources_count, len({str(item.source or '').strip().lower() for item in offers if str(item.source or '').strip()}))
        edge_pct = self._to_float_safe(market_signal.get('best_vs_consensus_edge_pct')) or 0.0
        delta_prob_pp = self._to_float_safe(market_signal.get('delta_prob_pp')) or 0.0
        dispersion_pct = self._to_float_safe(market_signal.get('consensus_dispersion_pct'))
        max_dispersion = float(os.getenv('MARKET_DERIVED_MAX_DISPERSION_PCT', '10.0') or 10.0)
        if books_count < 2 or sources_count < 1:
            return False
        if edge_pct < 0.9:
            return False
        if dispersion_pct is not None and dispersion_pct > max_dispersion:
            return False
        if family == 'h2h' and str(market_signal.get('selection_key') or '').strip().lower() == 'draw':
            return False
        return delta_prob_pp >= -0.25

    def _patched_simple_market_model_probability(self, *, family, market_prob, market_signal, books_count):
        result = original_simple_market_prob(self, family=family, market_prob=market_prob, market_signal=market_signal, books_count=books_count)
        if result is not None:
            return result
        market_prob = clamp(float(market_prob), 0.02, 0.98)
        edge_pct = self._to_float_safe((market_signal or {}).get('best_vs_consensus_edge_pct')) or 0.0
        steam_delta = self._to_float_safe((market_signal or {}).get('delta_prob_pp')) or 0.0
        dispersion_pct = self._to_float_safe((market_signal or {}).get('consensus_dispersion_pct'))
        if books_count < 2:
            return None
        if dispersion_pct is not None and dispersion_pct > 10.0:
            return None
        if edge_pct < 0.7 and steam_delta < 0.4:
            return None
        relaxed_boost_pct = 0.0
        relaxed_boost_pct += min(2.0, max(0.0, edge_pct) * 0.65)
        relaxed_boost_pct += min(1.2, max(0.0, steam_delta) * 0.50)
        if family == 'totals':
            relaxed_boost_pct += 0.45
        elif family == 'spreads':
            relaxed_boost_pct += 0.35
        elif family == 'h2h':
            relaxed_boost_pct += 0.20
        if relaxed_boost_pct < 0.75:
            return None
        cap = 3.5 if family == 'totals' else 3.0 if family == 'spreads' else 2.4
        return clamp(market_prob + min(cap, relaxed_boost_pct) / 100.0, 0.02, 0.98)

    def _patched_build_simple_market_h2h_candidates(self, match, offers, rejections):
        buckets = {}
        for offer in offers:
            buckets.setdefault(offer.selection, []).append(offer)
        result = []
        high_odds_skip_max = float(os.getenv('SIMPLE_MARKET_H2H_HIGH_ODDS_SKIP_MAX', '3.75') or 3.75)
        for selection, bucket in buckets.items():
            selection_key = self._h2h_selection_key(match, selection)
            if selection_key not in {'home', 'away'}:
                continue
            required_books = self._required_books_for_bucket('h2h', None, bucket, None)
            if len({self._norm_book(item.bookmaker) for item in bucket}) < required_books:
                continue
            best_offer = self._select_best_offer(bucket)
            if float(best_offer.price) >= high_odds_skip_max:
                rejections['simple_market_h2h_high_odds_skip'] += 1
                continue
            market_prob = self._fair_market_probability_h2h(match, offers, selection)
            market_signal = self._market_signal_for_bucket(match.match_key, 'h2h', bucket, None)
            model_prob = self._simple_market_model_probability(
                family='h2h',
                market_prob=market_prob,
                market_signal=market_signal,
                books_count=len({self._norm_book(item.bookmaker) for item in bucket}),
            )
            if model_prob is None:
                rejections['simple_market_signal_missing_h2h'] += 1
                continue
            candidate = self._candidate_from_bucket(
                match=match,
                family='h2h',
                selection=russian_selection('h2h', selection),
                point=None,
                offers=bucket,
                market_prob=market_prob,
                model_prob=model_prob,
                reasons=['mode=market_fallback', 'model=market_signal_consensus', 'signals=market+consensus', 'context=none'],
                expected_home=None,
                expected_away=None,
                model_mode='market_simple_h2h',
                context=None,
                market_signal=market_signal,
            )
            if candidate:
                result.append(candidate)
        return result

    CandidateFactory._market_signal_ready_for_derived = _patched_market_signal_ready_for_derived  # type: ignore[assignment]
    CandidateFactory._simple_market_model_probability = _patched_simple_market_model_probability  # type: ignore[assignment]
    CandidateFactory._build_simple_market_h2h_candidates = _patched_build_simple_market_h2h_candidates  # type: ignore[assignment]


_patch_api_football()
_patch_candidate_factory()
