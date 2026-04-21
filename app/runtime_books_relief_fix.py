from __future__ import annotations

import os
from typing import Any

_PATCH_APPLIED = False


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    try:
        return float(raw) if raw not in (None, '') else float(default)
    except Exception:
        return float(default)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw in (None, ''):
        return bool(default)
    return str(raw).strip().lower() in {'1', 'true', 'yes', 'on'}


def _patch_candidate_factory() -> None:
    from app.services.model import CandidateFactory

    if getattr(CandidateFactory, '_books_relief_patch_applied', False):
        return

    original_required_publish_books = CandidateFactory._required_publish_books
    original_passes_single_book_fallback_guard = CandidateFactory._passes_single_book_fallback_guard

    def _relief_enabled(self) -> bool:
        return _env_bool('CORE_SINGLE_BOOK_RELIEF_ENABLED', True)

    def _strong_single_book_core_signal(self, item: Any) -> bool:
        if not _relief_enabled(self):
            return False
        try:
            books_count = int(getattr(item, 'books_count', 0) or 0)
        except Exception:
            books_count = 0
        if books_count != 1:
            return False
        try:
            bucket = self._league_bucket(item)
        except Exception:
            bucket = 'other'
        if bucket not in {'preferred', 'secondary'}:
            return False
        try:
            if not self._has_core_context(item):
                return False
        except Exception:
            return False
        confidence = float(getattr(item, 'confidence', 0.0) or 0.0)
        edge_pct = float(getattr(item, 'edge_pct', 0.0) or 0.0)
        ev_pct = float(getattr(item, 'ev_pct', 0.0) or 0.0)
        publication_score = float(getattr(item, 'publication_score', 0.0) or 0.0)
        family = str(getattr(item, 'family', '') or '').strip().lower()
        source_summary = dict(getattr(item, 'source_summary', {}) or {})
        quality_score = float(source_summary.get('quality_score') or 0.0)
        selected_book = str(source_summary.get('selected_bookmaker') or getattr(item, 'bookmaker', '') or '').strip().lower()
        trusted_books = {'bet365', 'unibet', 'pinnacle', 'betfair'}
        if selected_book and selected_book not in trusted_books:
            return False
        # keep BTTS and non-heavy-shrink only when stronger than average
        shrink_gap_pp = abs(
            float(getattr(item, 'model_probability', 0.0) or 0.0)
            - float(getattr(item, 'adjusted_probability', 0.0) or 0.0)
        ) * 100.0
        if family == 'btts':
            min_conf = _env_float('CORE_SINGLE_BOOK_BTTS_MIN_CONFIDENCE', 63.0)
            min_ev = _env_float('CORE_SINGLE_BOOK_BTTS_MIN_EV_PCT', 3.0)
            min_edge = _env_float('CORE_SINGLE_BOOK_BTTS_MIN_EDGE_PCT', 3.5)
            min_score = _env_float('CORE_SINGLE_BOOK_BTTS_MIN_PUBLICATION_SCORE', 22.0)
            min_quality = _env_float('CORE_SINGLE_BOOK_BTTS_MIN_QUALITY_SCORE', 76.0)
        else:
            min_conf = _env_float('CORE_SINGLE_BOOK_RELIEF_MIN_CONFIDENCE', 58.0)
            min_ev = _env_float('CORE_SINGLE_BOOK_RELIEF_MIN_EV_PCT', 1.2)
            min_edge = _env_float('CORE_SINGLE_BOOK_RELIEF_MIN_EDGE_PCT', 1.8)
            min_score = _env_float('CORE_SINGLE_BOOK_RELIEF_MIN_PUBLICATION_SCORE', 10.0)
            min_quality = _env_float('CORE_SINGLE_BOOK_RELIEF_MIN_QUALITY_SCORE', 72.0)
        max_heavy_shrink = _env_float('CORE_SINGLE_BOOK_RELIEF_MAX_SHRINK_PP', 15.0)
        if shrink_gap_pp > max_heavy_shrink:
            return False
        return (
            confidence >= min_conf
            and ev_pct >= min_ev
            and edge_pct >= min_edge
            and publication_score >= min_score
            and quality_score >= min_quality
        )

    def patched_passes_single_book_fallback_guard(self, item: Any) -> bool:
        if _strong_single_book_core_signal(self, item):
            try:
                item.reasons.append('core_single_book_relief=enabled')
                if isinstance(getattr(item, 'source_summary', None), dict):
                    item.source_summary['core_single_book_relief'] = True
            except Exception:
                pass
            return True
        return original_passes_single_book_fallback_guard(self, item)

    def patched_required_publish_books(self, item: Any) -> int:
        required = original_required_publish_books(self, item)
        if required <= 1:
            return required
        if _strong_single_book_core_signal(self, item):
            return 1
        return required

    CandidateFactory._passes_single_book_fallback_guard = patched_passes_single_book_fallback_guard
    CandidateFactory._required_publish_books = patched_required_publish_books
    CandidateFactory._books_relief_patch_applied = True


def _apply() -> None:
    global _PATCH_APPLIED
    if _PATCH_APPLIED:
        return
    try:
        _patch_candidate_factory()
    except Exception:
        return
    _PATCH_APPLIED = True


_apply()
