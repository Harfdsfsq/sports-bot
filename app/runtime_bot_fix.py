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


def _harden_settings(settings: Any) -> None:
    # Keep staged enrichment, but do not strangle the whole candidate funnel.
    _safe_setattr(settings, 'enable_context_staging', True)

    # Publication safety for non-core stays stricter than core, but not so strict
    # that the model yields zero raw candidates for an entire workflow run.
    _raise_int(settings, 'non_core_league_min_books', 2)
    _raise_float(settings, 'non_core_league_min_confidence', 64.0)
    _raise_float(settings, 'non_core_league_min_edge_pct', 5.0)
    _raise_float(settings, 'non_core_league_min_ev_pct', 3.0)
    _safe_setattr(settings, 'non_core_league_require_core_context', True)

    # Quota protection, tuned to still leave enough context for a 12h workflow run.
    _cap_int(settings, 'api_football_context_match_limit', 24)
    try:
        current_predictions = getattr(settings, 'api_football_predictions_limit')
        if current_predictions in (None, ''):
            _safe_setattr(settings, 'api_football_predictions_limit', 12)
        else:
            _safe_setattr(settings, 'api_football_predictions_limit', min(int(current_predictions), 12))
    except Exception:
        _safe_setattr(settings, 'api_football_predictions_limit', 12)

    _cap_int(settings, 'football_data_context_match_limit', 120)
    _cap_int(settings, 'thesportsdb_context_match_limit', 80)
    _cap_int(settings, 'openfootball_context_match_limit', 120)
    _cap_int(settings, 'openligadb_context_match_limit', 24)
    _cap_int(settings, 'futrixmetrics_context_match_limit', 6)
    _cap_int(settings, 'newsapi_context_match_limit', 4)
    _cap_int(settings, 'newsapi_match_limit', 4)
    _cap_int(settings, 'newsapi_articles_per_match', 3)
    _cap_int(settings, 'gnews_context_match_limit', 3)
    _cap_int(settings, 'gnews_match_limit', 3)
    _cap_int(settings, 'gnews_articles_per_match', 2)


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

    # No single-source H2H publishes in non-core leagues.
    if is_non_core and family == 'h2h' and not _is_draw_selection(item) and sources_count < 2:
        rejections['runtime_guard_non_core_h2h_single_source'] = rejections.get('runtime_guard_non_core_h2h_single_source', 0) + 1
        return True

    # Single-source high-odds H2H is too noisy even outside the core pool.
    if family == 'h2h' and not _is_draw_selection(item) and odds >= 3.70 and sources_count < 2:
        rejections['runtime_guard_h2h_high_odds_single_source'] = rejections.get('runtime_guard_h2h_high_odds_single_source', 0) + 1
        return True

    # For non-core underdog H2H, keep a strong publication bar.
    if is_non_core and family == 'h2h' and not _is_draw_selection(item) and odds >= 3.40:
        if books_count < 2 or sources_count < 2 or confidence < 72.0 or edge_pct < 5.5 or ev_pct < 3.5:
            rejections['runtime_guard_non_core_h2h_high_odds'] = rejections.get('runtime_guard_non_core_h2h_high_odds', 0) + 1
            return True

    # News-only context is still not enough for non-core publish.
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

    original_runner_init = PredictionRunner.__init__
    original_filter_and_rank = CandidateFactory._filter_and_rank

    def patched_runner_init(self: Any, settings: Any) -> None:
        _harden_settings(settings)
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
