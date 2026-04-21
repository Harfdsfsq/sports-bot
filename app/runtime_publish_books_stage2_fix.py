from __future__ import annotations

from typing import Any

_PATCH_APPLIED = False


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ''):
            return default
        return float(value)
    except Exception:
        return default


def _norm_book(factory: Any, value: str) -> str:
    try:
        return factory._norm_book(value)
    except Exception:
        return str(value or '').strip().lower().replace(' ', '')


def _apply() -> None:
    global _PATCH_APPLIED
    if _PATCH_APPLIED:
        return
    try:
        from app.services.model import CandidateFactory
    except Exception:
        return

    if getattr(CandidateFactory, '_runtime_publish_books_stage2_fix_applied', False):
        _PATCH_APPLIED = True
        return

    original = CandidateFactory._required_publish_books

    def patched(self, item):
        required = original(self, item)
        try:
            books_count = int(getattr(item, 'books_count', 0) or 0)
            if books_count != 1:
                return required
            bucket = self._league_bucket(item)
            if bucket not in {'preferred', 'secondary'}:
                return required
            if not self._has_core_context(item):
                return required
            summary = dict(getattr(item, 'source_summary', {}) or {})
            bookmaker = str(summary.get('selected_bookmaker') or getattr(item, 'bookmaker', '') or '').strip()
            bookmaker_key = _norm_book(self, bookmaker)
            if bookmaker_key not in {_norm_book(self, x) for x in ('Bet365', 'Unibet', 'Pinnacle', 'Betfair')}:
                return required
            family = str(getattr(item, 'family', '') or '').strip().lower()
            confidence = float(getattr(item, 'confidence', 0.0) or 0.0)
            edge_pct = float(getattr(item, 'edge_pct', 0.0) or 0.0)
            ev_pct = float(getattr(item, 'ev_pct', 0.0) or 0.0)
            publication_score = float(getattr(item, 'publication_score', 0.0) or 0.0)
            quality_score = _to_float(summary.get('quality_score'))
            shrink_pp = abs(_to_float(getattr(item, 'model_probability', 0.0)) - _to_float(getattr(item, 'adjusted_probability', 0.0))) * 100.0
            sources_count = int(getattr(item, 'sources_count', 0) or 0)
            if family == 'btts':
                min_conf, min_edge, min_ev, min_pub, min_quality = 74.0, 5.0, 3.0, 35.0, 82.0
            else:
                min_conf, min_edge, min_ev, min_pub, min_quality = 70.0, 4.5, 2.5, 28.0, 80.0
            if sources_count <= 1 and shrink_pp >= 10.0:
                min_conf += 4.0
                min_edge += 1.0
                min_ev += 1.0
                min_pub += 8.0
                min_quality += 4.0
            if (
                confidence >= min_conf
                and edge_pct >= min_edge
                and ev_pct >= min_ev
                and publication_score >= min_pub
                and quality_score >= min_quality
            ):
                try:
                    item.reasons.append('publish_books_stage2_relief=single_book_core')
                    if isinstance(item.source_summary, dict):
                        item.source_summary['publish_books_stage2_relief'] = True
                except Exception:
                    pass
                return 1
        except Exception:
            return required
        return required

    CandidateFactory._required_publish_books = patched
    CandidateFactory._runtime_publish_books_stage2_fix_applied = True
    _PATCH_APPLIED = True


_apply()
