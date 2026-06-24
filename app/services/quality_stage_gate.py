from __future__ import annotations

import os
from typing import Any


def _on(name: str, default: bool = True) -> bool:
    raw = str(os.getenv(name) or '').strip().lower()
    if not raw:
        return default
    return raw in {'1', 'true', 'yes', 'on', 'force'}


def _num(value: Any, default: float = 0.0) -> float:
    try:
        return float(str(value).replace(',', '.')) if value not in (None, '') else default
    except Exception:
        return default


def _bridge_candidate(candidate: Any) -> bool:
    summary = getattr(candidate, 'source_summary', {}) or {}
    reasons = getattr(candidate, 'reasons', []) or []
    return bool(
        summary.get('market_signal_derived')
        or summary.get('controlled_prefilter_rescue')
        or any('controlled_prefilter_rescue' in str(item) for item in reasons)
    )


def _relief_allowed(candidate: Any) -> bool:
    return (
        _bridge_candidate(candidate)
        and _num(getattr(candidate, 'confidence', None)) >= _num(os.getenv('QUALITY_STAGE_GATE_MIN_CONFIDENCE'), 68.0)
        and _num(getattr(candidate, 'ev_pct', None)) >= _num(os.getenv('QUALITY_STAGE_GATE_MIN_EV_PCT'), 2.0)
        and _num(getattr(candidate, 'edge_pct', None)) >= _num(os.getenv('QUALITY_STAGE_GATE_MIN_EDGE_PP'), 1.5)
        and int(_num(getattr(candidate, 'books_count', None), 0.0)) >= int(_num(os.getenv('QUALITY_STAGE_GATE_MIN_BOOKS'), 1.0))
    )


def install() -> None:
    if not _on('QUALITY_STAGE_GATE_MARKET_BRIDGE_RELIEF_ENABLED', True):
        return
    from app.services.quality import PredictionQualityService

    if getattr(PredictionQualityService, '_harizon_quality_stage_gate_patch', False):
        return
    original = PredictionQualityService._post_calibration_threshold_guard

    def patched(self: Any, candidate: Any) -> str | None:
        reason = original(self, candidate)
        if reason in {'post_calibration_probability_guard', 'post_calibration_edge_guard', 'post_calibration_ev_guard'} and _relief_allowed(candidate):
            try:
                candidate.source_summary['quality_stage_gate_relief'] = {'original_reason': reason, 'mode': 'market_bridge_to_final_guards'}
                candidate.reasons.append(f'quality_stage_gate_relief={reason}')
            except Exception:
                pass
            return None
        return reason

    PredictionQualityService._post_calibration_threshold_guard = patched
    PredictionQualityService._harizon_quality_stage_gate_patch = True
