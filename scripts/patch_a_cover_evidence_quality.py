from __future__ import annotations

"""Allow fully evidenced A-cover promotions to be treated as evidence quality.

This patch does not lower A-tier evidence, value, movement, xG or price guards.
It only prevents candidates created from already A-ready inventory rows from being
classified as generic proxy quality when they still have 2 odds sources, 2 books
and 2 context confirmations at fallback evaluation time.
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

OUT = Path('.data/exports/latest-a-cover-evidence-quality-patch.json')


def _float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ''):
            return default
        return float(str(value).replace(',', '.'))
    except Exception:
        return default


def _int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ''):
            return default
        return int(float(str(value)))
    except Exception:
        return default


def _env_float(name: str, default: float) -> float:
    return _float(os.getenv(name), default)


def _is_a_cover_promotion(candidate: dict[str, Any]) -> bool:
    if str(candidate.get('_candidate_source') or '').strip().lower() == 'a_cover_market_promotion':
        return True
    summary = candidate.get('source_summary') if isinstance(candidate.get('source_summary'), dict) else {}
    if str(summary.get('selected_source') or '').strip().lower() == 'a_cover_market_promotion':
        return True
    diag = candidate.get('diagnostics') if isinstance(candidate.get('diagnostics'), dict) else {}
    if isinstance(diag.get('a_cover_promotion'), dict):
        return True
    return any('a_cover_market_promotion' in str(reason).lower() for reason in (candidate.get('reasons') or []))


def _has_strict_a_evidence(metrics: dict[str, Any]) -> bool:
    return (
        _int(metrics.get('odds_sources_count')) >= 2
        and _int(metrics.get('books_count')) >= 2
        and _int(metrics.get('confirmation_sources_count') or metrics.get('sources_count')) >= 2
    )


def _strong_enough_for_a_evidence(candidate: dict[str, Any], metrics: dict[str, Any]) -> bool:
    if not _is_a_cover_promotion(candidate) or not _has_strict_a_evidence(metrics):
        return False
    return (
        _float(metrics.get('canonical_ev_pct')) >= _env_float('CONTROLLED_FALLBACK_A_COVER_EVIDENCE_MIN_EV_PCT', 6.0)
        and _float(metrics.get('canonical_edge_pp')) >= _env_float('CONTROLLED_FALLBACK_A_COVER_EVIDENCE_MIN_EDGE_PP', 3.0)
        and _float(metrics.get('confidence')) >= _env_float('CONTROLLED_FALLBACK_A_COVER_EVIDENCE_MIN_CONFIDENCE', 68.0)
    )


def install(base_module: Any) -> dict[str, Any]:
    if getattr(base_module, '_harizon_a_cover_evidence_quality_patched', False):
        return {'status': 'already_installed'}
    original_quality_proxy = getattr(base_module, 'quality_proxy_score', None)
    if not callable(original_quality_proxy):
        return {'status': 'skipped', 'reason': 'quality_proxy_score_missing'}

    def quality_proxy_score_a_cover(candidate: dict[str, Any], metrics: dict[str, Any], raw_quality: float) -> tuple[float, str]:
        score, source = original_quality_proxy(candidate, metrics, raw_quality)
        if raw_quality > 0:
            return score, source
        if not _strong_enough_for_a_evidence(candidate, metrics):
            return score, source
        min_quality = _env_float('CONTROLLED_FALLBACK_A_COVER_EVIDENCE_MIN_QUALITY', 76.0)
        max_quality = _env_float('CONTROLLED_FALLBACK_A_COVER_EVIDENCE_MAX_QUALITY', 82.0)
        evidence_score = min(max_quality, max(_float(score), min_quality))
        try:
            diag = candidate.setdefault('diagnostics', {})
            if isinstance(diag, dict):
                diag['a_cover_evidence_quality'] = {
                    'enabled': True,
                    'created_at_utc': datetime.now(timezone.utc).isoformat(),
                    'quality_score': round(evidence_score, 3),
                    'source': 'a_cover_evidence',
                    'odds_sources_count': _int(metrics.get('odds_sources_count')),
                    'books_count': _int(metrics.get('books_count')),
                    'confirmation_sources_count': _int(metrics.get('confirmation_sources_count') or metrics.get('sources_count')),
                }
        except Exception:
            pass
        return round(evidence_score, 3), 'a_cover_evidence'

    base_module.quality_proxy_score = quality_proxy_score_a_cover
    base_module._harizon_a_cover_evidence_quality_patched = True
    payload = {
        'status': 'installed',
        'created_at_utc': datetime.now(timezone.utc).isoformat(),
        'policy': 'A-cover promotion can use a_cover_evidence quality only with 2 odds, 2 books, 2 context and strict EV/edge/confidence.',
        'min_ev_pct': _env_float('CONTROLLED_FALLBACK_A_COVER_EVIDENCE_MIN_EV_PCT', 6.0),
        'min_edge_pp': _env_float('CONTROLLED_FALLBACK_A_COVER_EVIDENCE_MIN_EDGE_PP', 3.0),
        'min_confidence': _env_float('CONTROLLED_FALLBACK_A_COVER_EVIDENCE_MIN_CONFIDENCE', 68.0),
    }
    try:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    except Exception:
        pass
    return payload
