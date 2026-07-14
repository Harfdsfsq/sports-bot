from __future__ import annotations

import os
from typing import Any


def _f(value: Any) -> float | None:
    try:
        if value in (None, ''):
            return None
        return float(str(value).replace(',', '.'))
    except Exception:
        return None


def _source_text(candidate: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in ('_candidate_source', 'candidate_source', 'quality_score_source'):
        value = candidate.get(key)
        if value not in (None, ''):
            parts.append(str(value))
    summary = candidate.get('source_summary') if isinstance(candidate.get('source_summary'), dict) else {}
    diagnostics = candidate.get('diagnostics') if isinstance(candidate.get('diagnostics'), dict) else {}
    for box in (summary, diagnostics):
        for key in ('selected_source', 'quality_score_source', 'created_by'):
            value = box.get(key) if isinstance(box, dict) else None
            if value not in (None, ''):
                parts.append(str(value))
    for key in ('a_cover_promotion', 'b_cover_promotion'):
        if isinstance(diagnostics.get(key), dict):
            parts.append(key)
    return ' '.join(parts).lower()


def _looks_like_proxy_promotion(candidate: dict[str, Any]) -> bool:
    text = _source_text(candidate)
    return any(token in text for token in ('proxy', 'promotion', 'a_cover', 'b_cover', 'awaiting_movement_lifecycle', 'market_promotion'))


def _hard_xg_marker(candidate: dict[str, Any]) -> bool:
    text = str(candidate).lower()
    return any(token in text for token in ('bzzoiro_stats', 'sstats_xg', 'xg_live', 'actual_home_xg', 'actual_away_xg', 'pre_match_home_xg', 'pre_match_away_xg'))


def _is_default_1_1(metrics: dict[str, Any], candidate: dict[str, Any]) -> bool:
    total = _f(metrics.get('xg_total'))
    home = _f(candidate.get('expected_home'))
    away = _f(candidate.get('expected_away'))
    if home is None or away is None:
        ctx = candidate.get('context') if isinstance(candidate.get('context'), dict) else {}
        home = home if home is not None else _f(ctx.get('expected_home'))
        away = away if away is not None else _f(ctx.get('expected_away'))
    if home is not None and away is not None:
        return abs(home - 1.0) < 1e-6 and abs(away - 1.0) < 1e-6
    return total is not None and abs(total - 2.0) < 1e-6


def install(base: Any) -> None:
    original = getattr(base, 'xg_sanity_metrics', None)
    if not callable(original) or getattr(base, '_proxy_default_xg_guard_installed', False):
        return

    def wrapped(candidate: dict[str, Any], adjusted_probability: float) -> dict[str, Any]:
        metrics = dict(original(candidate, adjusted_probability) or {})
        if str(os.getenv('CONTROLLED_FALLBACK_REJECT_PROXY_DEFAULT_XG') or 'true').strip().lower() not in {'1', 'true', 'yes', 'on', 'force'}:
            return metrics
        if not metrics.get('enabled'):
            return metrics
        fam = str(candidate.get('family') or candidate.get('market_family') or '').strip().lower()
        if fam not in {'totals', 'teamtotals'}:
            return metrics
        if _is_default_1_1(metrics, candidate) and _looks_like_proxy_promotion(candidate) and not _hard_xg_marker(candidate):
            metrics.update({
                'enabled': False,
                'reason': 'proxy_default_1_1_xg_placeholder',
                'xg_direction_ok': False,
                'proxy_default_xg_guard': {
                    'enabled': True,
                    'reason': '1.00:1.00 xG from proxy/promotion source is not hard xG evidence',
                    'requires_hard_xg_or_non_default_pair': True,
                },
            })
        return metrics

    base.xg_sanity_metrics = wrapped
    base._proxy_default_xg_guard_installed = True
