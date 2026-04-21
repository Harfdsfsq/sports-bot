from __future__ import annotations

import os
from typing import Any

_PATCH_APPLIED = False


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ''):
            return default
        return float(value)
    except Exception:
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == '':
        return float(default)
    try:
        return float(raw)
    except Exception:
        return float(default)


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == '':
        return int(default)
    try:
        return int(raw)
    except Exception:
        return int(default)


def _patch_candidate_factory() -> None:
    from app.services.model import CandidateFactory

    if getattr(CandidateFactory, '_runtime_current_cycle_fix_applied', False):
        return

    original_filter_and_rank = CandidateFactory._filter_and_rank

    def _filter_and_rank(self, candidates, rejections):
        filtered = list(original_filter_and_rank(self, candidates, rejections))
        result = []
        for item in filtered:
            source_summary = dict(getattr(item, 'source_summary', {}) or {})
            quality_score = _to_float(source_summary.get('quality_score'))
            publication_score = _to_float(getattr(item, 'publication_score', 0.0))
            confidence = _to_float(getattr(item, 'confidence', 0.0))
            ev_pct = _to_float(getattr(item, 'ev_pct', 0.0))
            edge_pct = _to_float(getattr(item, 'edge_pct', 0.0))
            sources_count = int(getattr(item, 'sources_count', 0) or 0)
            books_count = int(getattr(item, 'books_count', 0) or 0)
            family = str(getattr(item, 'family', '') or '').strip().lower()
            league_name = str(getattr(item, 'league_name', '') or '')
            shrink_pp = abs(_to_float(getattr(item, 'model_probability', 0.0)) - _to_float(getattr(item, 'adjusted_probability', 0.0))) * 100.0

            # Keep genuinely strong 2-book signals, but block weak single-source C-grade publishes.
            if sources_count <= 1:
                min_quality = _env_float('POSTFILTER_SINGLE_SOURCE_MIN_QUALITY', 72.0)
                min_pub_score = _env_float('POSTFILTER_SINGLE_SOURCE_MIN_PUBLICATION_SCORE', 35.0)
                min_conf = _env_float('POSTFILTER_SINGLE_SOURCE_MIN_CONFIDENCE', 70.0)
                min_ev = _env_float('POSTFILTER_SINGLE_SOURCE_MIN_EV_PCT', 3.0)
                heavy_shrink_pp = _env_float('POSTFILTER_SINGLE_SOURCE_HEAVY_SHRINK_MIN_PP', 8.0)
                heavy_shrink_min_ev = _env_float('POSTFILTER_SINGLE_SOURCE_HEAVY_SHRINK_MIN_EV_PCT', 6.0)
                heavy_shrink_min_quality = _env_float('POSTFILTER_SINGLE_SOURCE_HEAVY_SHRINK_MIN_QUALITY', 78.0)
                heavy_shrink_min_conf = _env_float('POSTFILTER_SINGLE_SOURCE_HEAVY_SHRINK_MIN_CONFIDENCE', 72.0)

                if family == 'btts':
                    min_quality = max(min_quality, _env_float('POSTFILTER_BTTS_SINGLE_SOURCE_MIN_QUALITY', 74.0))
                    min_pub_score = max(min_pub_score, _env_float('POSTFILTER_BTTS_SINGLE_SOURCE_MIN_PUBLICATION_SCORE', 34.0))
                    min_ev = max(min_ev, _env_float('POSTFILTER_BTTS_SINGLE_SOURCE_MIN_EV_PCT', 3.5))

                league_low = league_name.lower()
                if any(token in league_low for token in ['afc champions', 'international clubs', 'clubs -']):
                    min_quality = max(min_quality, 78.0)
                    min_pub_score = max(min_pub_score, 40.0)
                    min_ev = max(min_ev, 5.0)

                if books_count >= 2:
                    weak_single_source = (
                        quality_score < min_quality
                        and publication_score < min_pub_score
                        and (ev_pct < min_ev or confidence < min_conf or edge_pct < 3.0)
                    )
                    heavy_shrink_weak = (
                        shrink_pp >= heavy_shrink_pp
                        and (quality_score < heavy_shrink_min_quality or confidence < heavy_shrink_min_conf or ev_pct < heavy_shrink_min_ev)
                    )
                    if weak_single_source:
                        rejections['postfilter_single_source_quality_guard'] = int(rejections.get('postfilter_single_source_quality_guard', 0) or 0) + 1
                        continue
                    if heavy_shrink_weak:
                        rejections['postfilter_single_source_heavy_shrink_guard'] = int(rejections.get('postfilter_single_source_heavy_shrink_guard', 0) or 0) + 1
                        continue
            result.append(item)
        return result

    CandidateFactory._filter_and_rank = _filter_and_rank
    CandidateFactory._runtime_current_cycle_fix_applied = True


def _apply_env_defaults() -> None:
    defaults = {
        'MIN_SOURCES_PUBLISH': '1',
        'SUPPORTED_TOTAL_LINES': '0.5,0.75,1.0,1.25,1.5,1.75,2.0,2.25,2.5,2.75,3.0,3.25,3.5,3.75,4.0,4.25,4.5,4.75,5.0,5.25,5.5,5.75,6.0',
        'LINE_SUPPORT_TOLERANCE': '0.15',
        'MARKET_DERIVED_MIN_BOOKS': '1',
        'MARKET_DERIVED_MIN_SOURCES': '1',
        'MARKET_DERIVED_MIN_EDGE_PCT': '0.35',
        'MARKET_DERIVED_MIN_DELTA_PROB_PP': '-0.25',
        'MARKET_DERIVED_MAX_DISPERSION_PCT': '12.5',
        'MARKET_DERIVED_CONSENSUS_RELIEF_MIN_BOOKS': '1',
        'MARKET_DERIVED_CONSENSUS_RELIEF_MIN_SOURCES': '1',
        'MARKET_DERIVED_CONSENSUS_RELIEF_MIN_OBSERVATIONS': '1',
        'MARKET_DERIVED_CONSENSUS_RELIEF_MIN_EDGE_PCT': '0.35',
        'MARKET_DERIVED_CONSENSUS_RELIEF_MAX_DISPERSION_PCT': '12.5',
        'MARKET_DERIVED_CONSENSUS_RELIEF_PROBABILITY_BOOST_PCT': '1.35',
        'SIMPLE_MARKET_MIN_SIGNAL_BOOST_PCT': '0.05',
        'SIMPLE_MARKET_TOTALS_MIN_CONFIDENCE': '46',
        'SIMPLE_MARKET_TOTALS_MIN_EV_PCT': '0.45',
        'SIMPLE_MARKET_TOTALS_MIN_EDGE_PCT': '0.70',
        'SIMPLE_MARKET_SPREADS_MIN_CONFIDENCE': '48',
        'SIMPLE_MARKET_SPREADS_MIN_EV_PCT': '0.55',
        'SIMPLE_MARKET_SPREADS_MIN_EDGE_PCT': '0.85',
        'SIMPLE_MARKET_H2H_HIGH_ODDS_SKIP_MAX': '3.95',
        'POSTFILTER_SINGLE_SOURCE_MIN_QUALITY': '72.0',
        'POSTFILTER_SINGLE_SOURCE_MIN_PUBLICATION_SCORE': '35.0',
        'POSTFILTER_SINGLE_SOURCE_MIN_CONFIDENCE': '70.0',
        'POSTFILTER_SINGLE_SOURCE_MIN_EV_PCT': '3.0',
        'POSTFILTER_SINGLE_SOURCE_HEAVY_SHRINK_MIN_PP': '8.0',
        'POSTFILTER_SINGLE_SOURCE_HEAVY_SHRINK_MIN_QUALITY': '78.0',
        'POSTFILTER_SINGLE_SOURCE_HEAVY_SHRINK_MIN_CONFIDENCE': '72.0',
        'POSTFILTER_SINGLE_SOURCE_HEAVY_SHRINK_MIN_EV_PCT': '6.0',
        'POSTFILTER_BTTS_SINGLE_SOURCE_MIN_QUALITY': '74.0',
        'POSTFILTER_BTTS_SINGLE_SOURCE_MIN_PUBLICATION_SCORE': '34.0',
        'POSTFILTER_BTTS_SINGLE_SOURCE_MIN_EV_PCT': '3.5',
    }
    for key, value in defaults.items():
        os.environ.setdefault(key, value)


def _apply() -> None:
    global _PATCH_APPLIED
    if _PATCH_APPLIED:
        return
    _apply_env_defaults()
    _patch_candidate_factory()
    _PATCH_APPLIED = True


_apply()
