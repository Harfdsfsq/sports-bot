from __future__ import annotations

import os
from typing import Any

_PATCH_APPLIED = False


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ''):
            return default
        text = str(value).strip().replace('%', '').replace(',', '.')
        return float(text)
    except Exception:
        return default


def _league_is_international(league_name: str) -> bool:
    text = str(league_name or '').strip().lower()
    needles = (
        'international clubs',
        'champions league',
        'club friendly',
        'super cup',
        'cup',
        'afc',
        'uefa',
        'conmebol',
        'libertadores',
        'sudamericana',
    )
    return any(token in text for token in needles)


def _postfilter_should_drop(item: Any) -> bool:
    sources_count = int(getattr(item, 'sources_count', 0) or 0)
    if sources_count > 1:
        return False
    model_prob = float(getattr(item, 'model_probability', 0.0) or 0.0)
    adjusted_prob = float(getattr(item, 'adjusted_probability', 0.0) or 0.0)
    shrink_pp = abs(model_prob - adjusted_prob) * 100.0
    if shrink_pp < float(os.getenv('POSTFILTER_SINGLE_SOURCE_HEAVY_SHRINK_MIN_PP', '10.0') or 10.0):
        return False
    summary = dict(getattr(item, 'source_summary', {}) or {})
    quality_score = float(summary.get('quality_score') or 0.0)
    confidence = float(getattr(item, 'confidence', 0.0) or 0.0)
    ev_pct = float(getattr(item, 'ev_pct', 0.0) or 0.0)
    league_name = str(getattr(item, 'league_name', '') or '')
    if _league_is_international(league_name):
        return True
    if quality_score < float(os.getenv('POSTFILTER_SINGLE_SOURCE_HEAVY_SHRINK_MIN_QUALITY', '80.0') or 80.0):
        return True
    if confidence < float(os.getenv('POSTFILTER_SINGLE_SOURCE_HEAVY_SHRINK_MIN_CONFIDENCE', '70.0') or 70.0):
        return True
    if ev_pct < float(os.getenv('POSTFILTER_SINGLE_SOURCE_HEAVY_SHRINK_MIN_EV_PCT', '14.0') or 14.0):
        return True
    return False


def _patch_candidate_factory() -> None:
    from app.services.model import CandidateFactory

    if getattr(CandidateFactory, '_runtime_run_analysis_fix_applied', False):
        return

    original_filter_and_rank = CandidateFactory._filter_and_rank

    def _filter_and_rank(self, candidates, rejections):
        result = list(original_filter_and_rank(self, candidates, rejections))
        final = []
        for item in result:
            if _postfilter_should_drop(item):
                try:
                    rejections['postfilter_risky_single_source_heavy_shrink_guard'] += 1
                except Exception:
                    pass
                continue
            final.append(item)
        return final

    CandidateFactory._filter_and_rank = _filter_and_rank
    CandidateFactory._runtime_run_analysis_fix_applied = True


def _patch_telegram() -> None:
    from app.services.telegram import TelegramPublisher

    if getattr(TelegramPublisher, '_runtime_run_analysis_fix_applied', False):
        return

    async def publish(self, bets, bankroll_summary=None):
        if not bets:
            return 0, []
        message = self.render_message(bets, bankroll_summary=bankroll_summary)
        return await self._send_message(message)

    TelegramPublisher.publish = publish
    TelegramPublisher._runtime_run_analysis_fix_applied = True


def _apply() -> None:
    global _PATCH_APPLIED
    if _PATCH_APPLIED:
        return
    _patch_candidate_factory()
    _patch_telegram()
    _PATCH_APPLIED = True


_apply()
