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


def _quality_score(item: Any) -> float:
    summary = dict(getattr(item, 'source_summary', {}) or {})
    return _to_float(summary.get('quality_score'))


def _context_source(item: Any) -> str:
    summary = dict(getattr(item, 'source_summary', {}) or {})
    return str(summary.get('context_source') or '').strip().lower()


def _postfilter_enabled() -> bool:
    raw = str(os.getenv('POSTFILTER_SINGLE_SOURCE_HEAVY_SHRINK_ENABLED', 'true')).strip().lower()
    return raw not in {'0', 'false', 'no', 'off'}


def _apply() -> None:
    global _PATCH_APPLIED
    if _PATCH_APPLIED:
        return

    try:
        from app.services.model import CandidateFactory
    except Exception:
        return

    if getattr(CandidateFactory, '_publish_books_stage2_fix_applied', False):
        _PATCH_APPLIED = True
        return

    original_required_publish_books = CandidateFactory._required_publish_books
    original_single_book_guard = CandidateFactory._passes_single_book_fallback_guard

    def _passes_single_book_fallback_guard(self, item):
        books_count = int(getattr(item, 'books_count', 0) or 0)
        if books_count >= 2:
            return True
        if books_count <= 0:
            return False

        bucket = self._league_bucket(item)
        if bucket not in {'preferred', 'secondary'}:
            return False
        if not self._has_core_context(item):
            return False

        family = str(getattr(item, 'family', '') or '').strip().lower()
        confidence = float(getattr(item, 'confidence', 0.0) or 0.0)
        edge_pct = float(getattr(item, 'edge_pct', 0.0) or 0.0)
        ev_pct = float(getattr(item, 'ev_pct', 0.0) or 0.0)
        publication_score = float(getattr(item, 'publication_score', 0.0) or 0.0)
        quality_score = _quality_score(item)
        source_summary = dict(getattr(item, 'source_summary', {}) or {})
        sources_count = int(getattr(item, 'sources_count', 0) or 0)
        selected_book = str(source_summary.get('selected_bookmaker') or getattr(item, 'bookmaker', '') or '').strip().lower()
        shrink_pp = abs(
            float(getattr(item, 'model_probability', 0.0) or 0.0)
            - float(getattr(item, 'adjusted_probability', 0.0) or 0.0)
        ) * 100.0
        heavy_shrink = shrink_pp >= float(os.getenv('BOOKS_RELIEF_HEAVY_SHRINK_PP', '13.0') or 13.0)

        trusted_books = {'bet365', 'unibet', 'pinnacle', 'betfair'}
        if selected_book and selected_book not in trusted_books:
            return False

        # Stricter handling for BTTS and heavy-shrink situations.
        if family == 'btts':
            min_conf = float(os.getenv('BOOKS_RELIEF_BTTS_MIN_CONFIDENCE', '66.0') or 66.0)
            min_edge = float(os.getenv('BOOKS_RELIEF_BTTS_MIN_EDGE_PCT', '3.2') or 3.2)
            min_ev = float(os.getenv('BOOKS_RELIEF_BTTS_MIN_EV_PCT', '2.2') or 2.2)
            min_pub = float(os.getenv('BOOKS_RELIEF_BTTS_MIN_PUBLICATION_SCORE', '18.0') or 18.0)
            min_quality = float(os.getenv('BOOKS_RELIEF_BTTS_MIN_QUALITY_SCORE', '74.0') or 74.0)
        elif family in {'h2h', 'dnb', 'doubleChance', 'spreads'}:
            min_conf = float(os.getenv('BOOKS_RELIEF_SIDE_MIN_CONFIDENCE', '60.0') or 60.0)
            min_edge = float(os.getenv('BOOKS_RELIEF_SIDE_MIN_EDGE_PCT', '2.2') or 2.2)
            min_ev = float(os.getenv('BOOKS_RELIEF_SIDE_MIN_EV_PCT', '1.0') or 1.0)
            min_pub = float(os.getenv('BOOKS_RELIEF_SIDE_MIN_PUBLICATION_SCORE', '10.0') or 10.0)
            min_quality = float(os.getenv('BOOKS_RELIEF_SIDE_MIN_QUALITY_SCORE', '68.0') or 68.0)
        else:
            min_conf = float(os.getenv('BOOKS_RELIEF_TOTALS_MIN_CONFIDENCE', '60.0') or 60.0)
            min_edge = float(os.getenv('BOOKS_RELIEF_TOTALS_MIN_EDGE_PCT', '2.0') or 2.0)
            min_ev = float(os.getenv('BOOKS_RELIEF_TOTALS_MIN_EV_PCT', '0.9') or 0.9)
            min_pub = float(os.getenv('BOOKS_RELIEF_TOTALS_MIN_PUBLICATION_SCORE', '10.0') or 10.0)
            min_quality = float(os.getenv('BOOKS_RELIEF_TOTALS_MIN_QUALITY_SCORE', '68.0') or 68.0)

        # Secondary leagues need slightly stronger thresholds.
        if bucket == 'secondary':
            min_conf += 2.0
            min_edge += 0.5
            min_ev += 0.3
            min_pub += 1.5
            min_quality += 2.0

        if heavy_shrink:
            min_conf += float(os.getenv('BOOKS_RELIEF_HEAVY_SHRINK_CONF_BONUS', '4.0') or 4.0)
            min_edge += float(os.getenv('BOOKS_RELIEF_HEAVY_SHRINK_EDGE_BONUS', '1.5') or 1.5)
            min_ev += float(os.getenv('BOOKS_RELIEF_HEAVY_SHRINK_EV_BONUS', '1.5') or 1.5)
            min_pub += float(os.getenv('BOOKS_RELIEF_HEAVY_SHRINK_PUB_BONUS', '5.0') or 5.0)
            min_quality += float(os.getenv('BOOKS_RELIEF_HEAVY_SHRINK_QUALITY_BONUS', '6.0') or 6.0)

        if sources_count <= 1 and _postfilter_enabled() and heavy_shrink and quality_score < 78.0:
            return False

        return (
            confidence >= min_conf
            and edge_pct >= min_edge
            and ev_pct >= min_ev
            and publication_score >= min_pub
            and quality_score >= min_quality
        )

    def _required_publish_books(self, item):
        books_count = int(getattr(item, 'books_count', 0) or 0)
        if books_count >= 2:
            return 2
        if books_count == 1 and self._passes_single_book_fallback_guard(item):
            return 1
        return original_required_publish_books(self, item)

    CandidateFactory._passes_single_book_fallback_guard = _passes_single_book_fallback_guard
    CandidateFactory._required_publish_books = _required_publish_books
    CandidateFactory._publish_books_stage2_fix_applied = True
    _PATCH_APPLIED = True


_apply()
