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


def _to_int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ''):
            return default
        return int(float(value))
    except Exception:
        return default


def _source_summary(item: Any) -> dict[str, Any]:
    value = getattr(item, 'source_summary', None)
    return dict(value or {}) if isinstance(value, dict) else {}


def _quality_score(item: Any) -> float:
    summary = _source_summary(item)
    return _to_float(summary.get('quality_score'), _to_float(getattr(item, 'quality_score', None), 0.0))


def _league_bucket(settings: Any, league_name: str | None) -> str:
    try:
        if bool(getattr(settings, 'is_preferred_league')(league_name)):
            return 'preferred'
    except Exception:
        pass
    try:
        if bool(getattr(settings, 'is_secondary_league')(league_name)):
            return 'secondary'
    except Exception:
        pass
    try:
        if bool(getattr(settings, 'is_low_tier_league')(league_name)):
            return 'low'
    except Exception:
        pass
    return 'other'


def _family(item: Any) -> str:
    return str(getattr(item, 'family', '') or '').strip().lower()


def _shrink_pp(item: Any) -> float:
    model_p = _to_float(getattr(item, 'model_probability', None), 0.0)
    adj_p = _to_float(getattr(item, 'adjusted_probability', None), 0.0)
    return abs(model_p - adj_p) * 100.0


def _safe_set(item: Any, field: str, value: Any) -> None:
    try:
        setattr(item, field, value)
    except Exception:
        try:
            object.__setattr__(item, field, value)
        except Exception:
            pass


def _score_adjust(item: Any, settings: Any) -> None:
    family = _family(item)
    bucket = _league_bucket(settings, str(getattr(item, 'league_name', '') or ''))
    odds = _to_float(getattr(item, 'odds', None), 0.0)
    score = _to_float(getattr(item, 'publication_score', None), 0.0)
    if family in {'h2h', 'btts'}:
        score -= 1.6
    if bucket == 'secondary':
        score += 0.45
    if bucket in {'other', 'low'}:
        score -= 1.10
    if family in {'h2h', 'dnb', 'spreads'} and odds >= 3.35:
        score -= 0.90
    _safe_set(item, 'publication_score', round(score, 3))


def _bad_high_odds(item: Any, settings: Any) -> bool:
    family = _family(item)
    if family not in {'h2h', 'dnb', 'spreads'}:
        return False
    odds = _to_float(getattr(item, 'odds', None), 0.0)
    if odds < 3.35:
        return False
    sources = _to_int(getattr(item, 'sources_count', None), 0)
    if sources > 1:
        return False
    bucket = _league_bucket(settings, str(getattr(item, 'league_name', '') or ''))
    confidence = _to_float(getattr(item, 'confidence', None), 0.0)
    ev_pct = _to_float(getattr(item, 'ev_pct', None), 0.0)
    edge_pct = _to_float(getattr(item, 'edge_pct', None), 0.0)
    quality = _quality_score(item)
    shrink = _shrink_pp(item)
    elite_override = (
        bucket in {'preferred', 'secondary'}
        and confidence >= 76.0
        and ev_pct >= 12.0
        and edge_pct >= 6.5
        and quality >= 90.0
        and shrink <= 14.0
    )
    return not elite_override


def _bad_non_core(item: Any, settings: Any) -> bool:
    bucket = _league_bucket(settings, str(getattr(item, 'league_name', '') or ''))
    if bucket in {'preferred', 'secondary'}:
        return False
    confidence = _to_float(getattr(item, 'confidence', None), 0.0)
    ev_pct = _to_float(getattr(item, 'ev_pct', None), 0.0)
    edge_pct = _to_float(getattr(item, 'edge_pct', None), 0.0)
    quality = _quality_score(item)
    books = _to_int(getattr(item, 'books_count', None), 0)
    sources = _to_int(getattr(item, 'sources_count', None), 0)
    family = _family(item)
    allow = (
        confidence >= 74.0
        and ev_pct >= 6.0
        and edge_pct >= 8.5
        and quality >= 86.0
        and books >= 2
        and (sources >= 2 or family not in {'h2h', 'btts'})
    )
    return not allow


def _bad_single_source_h2h(item: Any) -> bool:
    if _family(item) != 'h2h':
        return False
    if _to_int(getattr(item, 'sources_count', None), 0) > 1:
        return False
    confidence = _to_float(getattr(item, 'confidence', None), 0.0)
    ev_pct = _to_float(getattr(item, 'ev_pct', None), 0.0)
    edge_pct = _to_float(getattr(item, 'edge_pct', None), 0.0)
    quality = _quality_score(item)
    return confidence < 73.0 or ev_pct < 4.0 or edge_pct < 5.0 or quality < 80.0


def _bad_single_source_btts(item: Any) -> bool:
    if _family(item) != 'btts':
        return False
    if _to_int(getattr(item, 'sources_count', None), 0) > 1:
        return False
    confidence = _to_float(getattr(item, 'confidence', None), 0.0)
    ev_pct = _to_float(getattr(item, 'ev_pct', None), 0.0)
    edge_pct = _to_float(getattr(item, 'edge_pct', None), 0.0)
    quality = _quality_score(item)
    odds = _to_float(getattr(item, 'odds', None), 0.0)
    return confidence < 74.0 or ev_pct < 4.0 or edge_pct < 5.0 or quality < 78.0 or odds > 2.40


def _apply() -> None:
    global _PATCH_APPLIED
    if _PATCH_APPLIED:
        return
    try:
        from app.services.model import CandidateFactory
    except Exception:
        return
    if getattr(CandidateFactory, '_runtime_daily_risk_fix_applied', False):
        _PATCH_APPLIED = True
        return

    original_filter = CandidateFactory._filter_and_rank

    def patched_filter(self, candidates, rejections):
        adjusted = []
        for item in candidates:
            _score_adjust(item, getattr(self, 'settings', None))
            adjusted.append(item)
        result = original_filter(self, adjusted, rejections)
        filtered = []
        settings = getattr(self, 'settings', None)
        for item in result:
            if _bad_high_odds(item, settings):
                rejections['postfilter_high_odds_daily_guard'] += 1
                continue
            if _bad_non_core(item, settings):
                rejections['postfilter_non_core_daily_guard'] += 1
                continue
            if _bad_single_source_h2h(item):
                rejections['postfilter_h2h_daily_guard'] += 1
                continue
            if _bad_single_source_btts(item):
                rejections['postfilter_btts_daily_guard'] += 1
                continue
            filtered.append(item)
        return filtered

    CandidateFactory._filter_and_rank = patched_filter
    CandidateFactory._runtime_daily_risk_fix_applied = True
    _PATCH_APPLIED = True


_apply()
