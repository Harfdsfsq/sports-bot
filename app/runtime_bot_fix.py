from __future__ import annotations

from typing import Any

_APPLIED = False


def _safe_setattr(obj: Any, name: str, value: Any) -> None:
    try:
        object.__setattr__(obj, name, value)
    except Exception:
        try:
            setattr(obj, name, value)
        except Exception:
            return


def _cap_int(settings: Any, name: str, max_value: int) -> None:
    try:
        current = int(getattr(settings, name))
    except Exception:
        return
    _safe_setattr(settings, name, min(current, max_value))


def _raise_int(settings: Any, name: str, min_value: int) -> None:
    try:
        current = int(getattr(settings, name))
    except Exception:
        return
    _safe_setattr(settings, name, max(current, min_value))


def _raise_float(settings: Any, name: str, min_value: float) -> None:
    try:
        current = float(getattr(settings, name))
    except Exception:
        return
    _safe_setattr(settings, name, max(current, min_value))


def _normalize_api_football_float(value: Any) -> float | None:
    try:
        if value in (None, ''):
            return None
        text = str(value).strip().replace('%', '').replace(',', '.')
        if not text:
            return None
        return float(text)
    except Exception:
        return None


def _coverage_tune_settings(settings: Any) -> None:
    # Keep staged selection, but allow cheap context providers to work even when
    # a match has no immediate target-book offers yet.
    _safe_setattr(settings, 'enable_context_staging', True)
    _safe_setattr(settings, 'context_enrichment_requires_offers', False)
    _raise_int(settings, 'context_enrichment_match_limit', 72)
    _raise_int(settings, 'premium_context_shortlist_limit', 24)
    _raise_int(settings, 'premium_news_shortlist_limit', 3)

    # Preserve publication safety from the previous fix.
    _raise_int(settings, 'min_sources_publish', 2)
    _raise_int(settings, 'min_books_publish', 2)
    _raise_int(settings, 'non_core_league_min_books', 2)
    _raise_float(settings, 'non_core_league_min_confidence', 66.0)
    _raise_float(settings, 'non_core_league_min_edge_pct', 6.0)
    _raise_float(settings, 'non_core_league_min_ev_pct', 4.0)
    _safe_setattr(settings, 'non_core_league_require_core_context', True)

    # Expand cheap / structural context providers so the bot sees more matches.
    _raise_int(settings, 'bzzoiro_context_match_limit', 96)
    _raise_int(settings, 'espn_context_match_limit', 48)
    _raise_int(settings, 'thesportsdb_context_match_limit', 96)
    _raise_int(settings, 'football_data_context_match_limit', 120)
    _raise_int(settings, 'openligadb_context_match_limit', 48)
    _raise_int(settings, 'openfootball_context_match_limit', 120)
    _raise_int(settings, 'futrixmetrics_context_match_limit', 8)

    # Premium providers stay bounded so free tiers do not burn out.
    _cap_int(settings, 'api_football_context_match_limit', 24)
    try:
        current_predictions = getattr(settings, 'api_football_predictions_limit')
        if current_predictions in (None, ''):
            _safe_setattr(settings, 'api_football_predictions_limit', 12)
        else:
            _safe_setattr(settings, 'api_football_predictions_limit', min(int(current_predictions), 12))
    except Exception:
        _safe_setattr(settings, 'api_football_predictions_limit', 12)

    _cap_int(settings, 'newsapi_context_match_limit', 4)
    _cap_int(settings, 'newsapi_match_limit', 4)
    _cap_int(settings, 'newsapi_articles_per_match', 3)
    _cap_int(settings, 'gnews_context_match_limit', 2)
    _cap_int(settings, 'gnews_match_limit', 2)
    _cap_int(settings, 'gnews_articles_per_match', 2)

    # Slightly larger price scan without going wide-open.
    _raise_int(settings, 'max_matches_for_odds_fetch', 220)


def _is_draw_selection(item: Any) -> bool:
    text = str(getattr(item, 'selection', '') or '').strip().lower()
    return text in {'draw', 'x', 'ничья'}


def _extra_publish_guard(self: Any, item: Any, rejections: dict[str, int]) -> bool:
    league_bucket = 'other'
    try:
        league_bucket = str(self._league_bucket(item) or 'other')
    except Exception:
        league_bucket = 'other'
    is_non_core = league_bucket not in {'preferred', 'secondary'}

    try:
        books_count = int(getattr(item, 'books_count', 0) or 0)
    except Exception:
        books_count = 0
    try:
        sources_count = int(getattr(item, 'sources_count', 0) or 0)
    except Exception:
        sources_count = 0
    try:
        odds = float(getattr(item, 'odds', 0.0) or 0.0)
    except Exception:
        odds = 0.0
    try:
        confidence = float(getattr(item, 'confidence', 0.0) or 0.0)
    except Exception:
        confidence = 0.0
    try:
        edge_pct = float(getattr(item, 'edge_pct', 0.0) or 0.0)
    except Exception:
        edge_pct = 0.0
    try:
        ev_pct = float(getattr(item, 'ev_pct', 0.0) or 0.0)
    except Exception:
        ev_pct = 0.0

    family = str(getattr(item, 'family', '') or '')
    source_summary = dict(getattr(item, 'source_summary', {}) or {})
    context_source = str(source_summary.get('context_source') or '').strip().lower()
    analysis_flags = {str(v).strip().lower() for v in ((getattr(item, 'analysis', {}) or {}).get('flags') or [])}

    if is_non_core and sources_count < 2:
        rejections['runtime_guard_non_core_single_source'] = rejections.get('runtime_guard_non_core_single_source', 0) + 1
        return True
    if family == 'h2h' and not _is_draw_selection(item) and odds >= 3.40 and sources_count < 2:
        rejections['runtime_guard_h2h_high_odds_single_source'] = rejections.get('runtime_guard_h2h_high_odds_single_source', 0) + 1
        return True
    if is_non_core and family == 'h2h' and not _is_draw_selection(item) and odds >= 3.20:
        if books_count < 2 or sources_count < 2 or confidence < 75.0 or edge_pct < 6.0 or ev_pct < 4.0:
            rejections['runtime_guard_non_core_h2h_high_odds'] = rejections.get('runtime_guard_non_core_h2h_high_odds', 0) + 1
            return True
    if is_non_core and context_source in {'newsapi', 'gnews'} and 'table' not in analysis_flags and 'xg' not in analysis_flags:
        rejections['runtime_guard_non_core_news_only_context'] = rejections.get('runtime_guard_non_core_news_only_context', 0) + 1
        return True
    return False


def apply_runtime_fixes() -> None:
    global _APPLIED
    if _APPLIED:
        return
    _APPLIED = True

    from app.services.model import CandidateFactory
    from app.services.runner import PredictionRunner
    from app.providers.api_football import ApiFootballContextProvider

    original_runner_init = PredictionRunner.__init__
    original_filter_and_rank = CandidateFactory._filter_and_rank

    def patched_runner_init(self: Any, settings: Any) -> None:
        _coverage_tune_settings(settings)
        original_runner_init(self, settings)

    def patched_filter_and_rank(self: Any, candidates: list[Any], rejections: dict[str, int]) -> list[Any]:
        guarded_candidates: list[Any] = []
        for item in candidates:
            if _extra_publish_guard(self, item, rejections):
                continue
            guarded_candidates.append(item)
        return original_filter_and_rank(self, guarded_candidates, rejections)

    PredictionRunner.__init__ = patched_runner_init
    CandidateFactory._filter_and_rank = patched_filter_and_rank
    ApiFootballContextProvider._to_float = staticmethod(_normalize_api_football_float)
