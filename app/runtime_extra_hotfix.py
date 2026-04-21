from __future__ import annotations

import os
from typing import Any

_PATCH_APPLIED = False


def _to_float_any(value: Any, default: float | None = None) -> float | None:
    try:
        if value in (None, ""):
            return default
        text = str(value).strip().replace(",", ".")
        if text.endswith("%"):
            text = text[:-1].strip()
        if text in {"", ".", "-", "+"}:
            return default
        return float(text)
    except Exception:
        return default


def apply_runtime_hotfix() -> None:
    global _PATCH_APPLIED
    if _PATCH_APPLIED:
        return
    _PATCH_APPLIED = True

    try:
        from app.providers.api_football import ApiFootballContextProvider

        def _patched_to_float(value: Any) -> float | None:
            return _to_float_any(value, None)

        ApiFootballContextProvider._to_float = staticmethod(_patched_to_float)
    except Exception:
        pass

    try:
        from app.providers.newsapi import NewsApiContextProvider

        _orig_cooldown_until = NewsApiContextProvider._cooldown_until

        def _patched_cooldown_until(self, provider: str = "newsapi"):
            return _orig_cooldown_until(self, provider)

        NewsApiContextProvider._cooldown_until = _patched_cooldown_until
    except Exception:
        pass

    try:
        from app.services.model import CandidateFactory
        from app.utils import clamp, russian_selection
        from collections import defaultdict

        _orig_ready = CandidateFactory._market_signal_ready_for_derived
        _orig_simple_market_model_probability = CandidateFactory._simple_market_model_probability

        def _patched_market_signal_ready_for_derived(self, family: str, market_signal: dict[str, Any] | None, offers):
            if _orig_ready(self, family, market_signal, offers):
                return True
            if not isinstance(market_signal, dict):
                return False
            books_count = int(market_signal.get('books_count') or 0)
            sources_count = int(market_signal.get('sources_count') or 0)
            if offers is not None:
                books_count = max(books_count, len({self._norm_book(item.bookmaker) for item in offers if str(getattr(item, 'bookmaker', '') or '').strip()}))
                sources_count = max(sources_count, len({str(getattr(item, 'source', '') or '').strip().lower() for item in offers if str(getattr(item, 'source', '') or '').strip()}))
            edge_pct = self._to_float_safe(market_signal.get('best_vs_consensus_edge_pct')) or 0.0
            delta_prob_pp = self._to_float_safe(market_signal.get('delta_prob_pp')) or 0.0
            dispersion_pct = self._to_float_safe(market_signal.get('consensus_dispersion_pct'))
            selection_key = str(market_signal.get('selection_key') or '').strip().lower()
            if family == 'h2h' and selection_key == 'draw':
                return False
            relaxed_min_edge = float(os.getenv('RELAXED_MARKET_DERIVED_MIN_EDGE_PCT') or 0.55)
            relaxed_max_disp = float(os.getenv('RELAXED_MARKET_DERIVED_MAX_DISPERSION_PCT') or 12.5)
            relaxed_min_books = max(1, int(os.getenv('RELAXED_MARKET_DERIVED_MIN_BOOKS') or 1))
            relaxed_min_sources = max(1, int(os.getenv('RELAXED_MARKET_DERIVED_MIN_SOURCES') or 1))
            if family == 'h2h':
                relaxed_min_edge = max(relaxed_min_edge, float(os.getenv('RELAXED_MARKET_DERIVED_H2H_MIN_EDGE_PCT') or 0.80))
                relaxed_max_disp = float(os.getenv('RELAXED_MARKET_DERIVED_H2H_MAX_DISPERSION_PCT') or 10.5)
            if books_count < relaxed_min_books or sources_count < relaxed_min_sources:
                return False
            if dispersion_pct is not None and dispersion_pct > relaxed_max_disp:
                return False
            if edge_pct < relaxed_min_edge:
                return False
            if delta_prob_pp < float(os.getenv('RELAXED_MARKET_DERIVED_MIN_DELTA_PROB_PP') or -0.35):
                return False
            return True

        def _patched_simple_market_model_probability(self, *, family: str, market_prob: float, market_signal: dict[str, Any] | None, books_count: int):
            value = _orig_simple_market_model_probability(
                self,
                family=family,
                market_prob=market_prob,
                market_signal=market_signal,
                books_count=books_count,
            )
            if value is not None:
                return value
            market_prob = clamp(float(market_prob), 0.02, 0.98)
            edge_pct = self._to_float_safe((market_signal or {}).get('best_vs_consensus_edge_pct')) or 0.0
            steam_delta = self._to_float_safe((market_signal or {}).get('delta_prob_pp')) or 0.0
            dispersion_pct = self._to_float_safe((market_signal or {}).get('consensus_dispersion_pct'))
            relaxed_edge_floor = float(os.getenv('RELAXED_SIMPLE_MARKET_MIN_EDGE_PCT') or 0.35)
            relaxed_max_disp = float(os.getenv('RELAXED_SIMPLE_MARKET_MAX_DISPERSION_PCT') or 14.0)
            if edge_pct < relaxed_edge_floor:
                return None
            if dispersion_pct is not None and dispersion_pct > relaxed_max_disp:
                return None
            signal_boost_pct = max(0.40, edge_pct * 0.60)
            if steam_delta > 0:
                signal_boost_pct += min(1.25, steam_delta * 0.35)
            if books_count >= 2:
                signal_boost_pct += 0.35
            family_cap = 3.6 if family == 'totals' else 3.1 if family == 'h2h' else 2.8
            return clamp(market_prob + min(family_cap, signal_boost_pct) / 100.0, 0.02, 0.98)

        def _patched_build_simple_market_h2h_candidates(self, match, offers, rejections):
            buckets = defaultdict(list)
            for offer in offers:
                buckets[offer.selection].append(offer)
            result = []
            high_odds_skip = float(os.getenv('SIMPLE_MARKET_H2H_HIGH_ODDS_SKIP_AT') or 4.10)
            for selection, bucket in buckets.items():
                selection_key = self._h2h_selection_key(match, selection)
                if selection_key not in {'home', 'away'}:
                    continue
                required_books = self._required_books_for_bucket('h2h', None, bucket, None)
                if len({self._norm_book(item.bookmaker) for item in bucket}) < required_books:
                    continue
                best_offer = self._select_best_offer(bucket)
                if float(best_offer.price) >= high_odds_skip and len(bucket) <= 1:
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
                    reasons=[
                        'mode=market_fallback',
                        'model=market_signal_consensus_relaxed',
                        'signals=market+consensus',
                        'context=none',
                    ],
                    expected_home=None,
                    expected_away=None,
                    model_mode='market_simple_h2h',
                    context=None,
                    market_signal=market_signal,
                )
                if candidate:
                    result.append(candidate)
            return result

        CandidateFactory._market_signal_ready_for_derived = _patched_market_signal_ready_for_derived
        CandidateFactory._simple_market_model_probability = _patched_simple_market_model_probability
        CandidateFactory._build_simple_market_h2h_candidates = _patched_build_simple_market_h2h_candidates
    except Exception:
        pass


apply_runtime_hotfix()
