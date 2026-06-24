from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


CONTEXT_INDEX = Path('.data/exports/latest-context-source-index.json')


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


def _norm(value: Any) -> str:
    return ' '.join(''.join(ch.lower() if ch.isalnum() else ' ' for ch in str(value or '').replace('_', ' ')).split())


def _date(value: Any) -> str:
    text = str(value or '')
    for idx in range(max(0, len(text) - 9)):
        part = text[idx:idx + 10]
        if len(part) == 10 and part[4] == '-' and part[7] == '-' and part[:4].isdigit():
            return part
    return ''


def _candidate_context_keys(candidate: Any) -> list[str]:
    raw_key = str(getattr(candidate, 'match_key', '') or '')
    keys = [raw_key] if raw_key else []
    home = _norm(getattr(candidate, 'home_team', ''))
    away = _norm(getattr(candidate, 'away_team', ''))
    day = _date(getattr(candidate, 'commence_time', '')) or _date(raw_key)
    if home and away and day:
        keys.append(f'soccer|{home}|{away}|{day}')
        keys.append(f'soccer|{away}|{home}|{day}')
    return list(dict.fromkeys(keys))


def _load_context_index() -> dict[str, list[str]]:
    try:
        if not CONTEXT_INDEX.exists() or CONTEXT_INDEX.stat().st_size <= 0:
            return {}
        payload = json.loads(CONTEXT_INDEX.read_text(encoding='utf-8'))
        by_match = payload.get('by_match') if isinstance(payload, dict) else {}
        return by_match if isinstance(by_match, dict) else {}
    except Exception:
        return {}


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


def _install_quality_stage_gate() -> None:
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


def _install_context_index_bridge() -> None:
    if not _on('PUBLISH_COVERAGE_CONTEXT_INDEX_BRIDGE_ENABLED', True):
        return
    from app.services import coverage_contract

    if getattr(coverage_contract, '_harizon_context_index_bridge_patch', False):
        return
    original = coverage_contract.context_sources_for_candidate

    def context_sources_with_index(candidate: Any) -> set[str]:
        sources = set(original(candidate) or set())
        if sources:
            return sources
        index = _load_context_index()
        for key in _candidate_context_keys(candidate):
            found = index.get(key)
            if isinstance(found, list):
                sources.update(str(item) for item in found if str(item).strip())
        normalized = {coverage_contract.normalize_source(item) for item in sources}
        return {item for item in normalized if item and item not in coverage_contract.AGGREGATE_CONTEXT_SOURCES}

    coverage_contract.context_sources_for_candidate = context_sources_with_index
    coverage_contract._harizon_context_index_bridge_patch = True


def install() -> None:
    if _on('QUALITY_STAGE_GATE_MARKET_BRIDGE_RELIEF_ENABLED', True):
        _install_quality_stage_gate()
    _install_context_index_bridge()
