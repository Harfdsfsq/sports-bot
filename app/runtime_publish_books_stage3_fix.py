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


def _league_bucket(factory: Any, item: Any) -> str:
    try:
        return str(factory._league_bucket(item) or '').lower()
    except Exception:
        return ''


def _quality_score(item: Any) -> float:
    summary = dict(getattr(item, 'source_summary', {}) or {})
    return _to_float(summary.get('quality_score'), 0.0)


def _context_source(item: Any) -> str:
    summary = dict(getattr(item, 'source_summary', {}) or {})
    return str(summary.get('context_source') or '').lower()


def _heavy_shrink(item: Any) -> bool:
    raw = _to_float(getattr(item, 'model_probability', 0.0), 0.0)
    adj = _to_float(getattr(item, 'adjusted_probability', 0.0), 0.0)
    return abs(raw - adj) * 100.0 >= 9.0


def _preferred_book(item: Any) -> bool:
    summary = dict(getattr(item, 'source_summary', {}) or {})
    books = [str(v).strip().lower() for v in (summary.get('books') or []) if str(v).strip()]
    selected = str(summary.get('selected_bookmaker') or getattr(item, 'bookmaker', '') or '').strip().lower()
    hay = books + ([selected] if selected else [])
    return any(name in {'bet365', 'unibet', 'pinnacle', 'betfair'} for name in hay)


def _late_window_relief_ok(factory: Any, item: Any) -> bool:
    if int(getattr(item, 'books_count', 0) or 0) != 1:
        return False
    bucket = _league_bucket(factory, item)
    if bucket not in {'preferred', 'secondary'}:
        return False
    try:
        if not bool(factory._has_core_context(item)):
            return False
    except Exception:
        return False
    family = str(getattr(item, 'family', '') or '').lower()
    conf = _to_float(getattr(item, 'confidence', 0.0), 0.0)
    edge = _to_float(getattr(item, 'edge_pct', 0.0), 0.0)
    ev = _to_float(getattr(item, 'ev_pct', 0.0), 0.0)
    pub = _to_float(getattr(item, 'publication_score', 0.0), 0.0)
    qual = _quality_score(item)
    odds = _to_float(getattr(item, 'odds', 0.0), 0.0)
    if odds <= 1.0:
        return False
    if not _preferred_book(item):
        return False
    if _heavy_shrink(item):
        # keep heavy shrink strict
        return False
    # Stricter for BTTS and very high odds sides
    if family == 'btts':
        return conf >= 73.0 and edge >= 4.5 and ev >= 3.0 and pub >= 20.0 and qual >= 84.0 and odds <= 2.30
    if family in {'h2h', 'dnb', 'spreads'} and odds >= 3.60:
        return conf >= 74.0 and edge >= 5.5 and ev >= 4.0 and pub >= 18.0 and qual >= 84.0
    # Core / secondary late-window relief
    return conf >= 69.0 and edge >= 3.8 and ev >= 2.2 and pub >= 12.5 and qual >= 80.0



def _apply() -> None:
    global _PATCH_APPLIED
    if _PATCH_APPLIED:
        return
    try:
        from app.services.model import CandidateFactory
    except Exception:
        return
    if not getattr(CandidateFactory, '_runtime_publish_books_stage3_fix_applied', False):
        original = CandidateFactory._passes_single_book_fallback_guard

        def patched(self, item):
            try:
                if original(self, item):
                    return True
            except Exception:
                pass
            if _late_window_relief_ok(self, item):
                try:
                    item.reasons.append('single_book_relief_stage3')
                    if isinstance(item.source_summary, dict):
                        item.source_summary['single_book_relief_stage3'] = True
                except Exception:
                    pass
                return True
            return False

        CandidateFactory._passes_single_book_fallback_guard = patched
        CandidateFactory._runtime_publish_books_stage3_fix_applied = True
    _PATCH_APPLIED = True


_apply()
