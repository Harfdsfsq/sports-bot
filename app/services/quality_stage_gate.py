from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


CONTEXT_INDEX = Path('.data/exports/latest-context-source-index.json')
INVENTORY_CONTEXT_PATHS = (
    Path('.data/day_inventory/latest.json'),
    Path('.data/day_inventory/current.json'),
    Path('artifacts/run-bot/day_inventory/latest.json'),
    Path('artifacts/run-bot/day_inventory-latest.json'),
)
NON_MATCH_CONTEXT_SOURCES = {'ensemble', 'market', 'market_signal', 'unknown'}


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


def _count(value: Any) -> int:
    try:
        if value in (None, ''):
            return 0
        if isinstance(value, (list, tuple, set, dict)):
            return len(value)
        return int(float(str(value).replace(',', '.')))
    except Exception:
        return 0


def _split_sources(value: Any) -> set[str]:
    if value in (None, ''):
        return set()
    if isinstance(value, str):
        return {part.strip() for part in value.replace(';', ',').replace('|', ',').split(',') if part.strip()}
    if isinstance(value, (list, tuple, set)):
        out: set[str] = set()
        for item in value:
            out.update(_split_sources(item))
        return out
    if isinstance(value, dict):
        return {str(key).strip() for key in value.keys() if str(key).strip()}
    return set()


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


def _row_context_sources(row: dict[str, Any]) -> set[str]:
    sources: set[str] = set()
    for container in (row, row.get('coverage'), row.get('metadata')):
        if not isinstance(container, dict):
            continue
        for key in ('context_sources', 'context_confirmations', 'all_context_sources', 'core_context_sources', 'supplemental_context_sources'):
            sources.update(_split_sources(container.get(key)))
        for count_key in ('context_sources_count', 'context_confirmations_count', 'confirmation_sources_count', 'context_source_count'):
            if _count(container.get(count_key)) >= 1:
                sources.add('inventory_context')
        if container.get('context') or container.get('ready_for_model') or container.get('coverage_context'):
            sources.add('inventory_context')
    return sources


def _load_inventory_context_index() -> dict[str, list[str]]:
    if not _on('PUBLISH_COVERAGE_INVENTORY_CONTEXT_FALLBACK_ENABLED', True):
        return {}
    out: dict[str, set[str]] = {}
    for path in INVENTORY_CONTEXT_PATHS:
        try:
            if not path.exists() or path.stat().st_size <= 0:
                continue
            payload = json.loads(path.read_text(encoding='utf-8'))
            rows = payload.get('matches') if isinstance(payload, dict) else payload
            if not isinstance(rows, list):
                continue
            for row in rows:
                if not isinstance(row, dict):
                    continue
                sources = _row_context_sources(row)
                if not sources:
                    continue
                raw_key = str(row.get('match_key') or row.get('canonical_match_id') or '')
                home = _norm(row.get('home_team') or row.get('home'))
                away = _norm(row.get('away_team') or row.get('away'))
                day = _date(row.get('kickoff_utc') or row.get('commence_time') or row.get('date_local') or raw_key)
                keys = [raw_key] if raw_key else []
                if home and away and day:
                    keys.extend([f'soccer|{home}|{away}|{day}', f'soccer|{away}|{home}|{day}'])
                for key in keys:
                    if key:
                        out.setdefault(key, set()).update(sources)
        except Exception:
            continue
    return {key: sorted(value) for key, value in out.items()}


def _bridge_candidate(candidate: Any) -> bool:
    summary = getattr(candidate, 'source_summary', {}) or {}
    reasons = getattr(candidate, 'reasons', []) or []
    return bool(
        summary.get('market_signal_derived')
        or summary.get('controlled_prefilter_rescue')
        or summary.get('controlled_rescue_append')
        or summary.get('rescue_file_append_bridge')
        or any('controlled_prefilter_rescue' in str(item) or 'controlled_rescue' in str(item) or 'rescue_file_append' in str(item) for item in reasons)
    )


def _relief_allowed(candidate: Any) -> bool:
    return (
        _bridge_candidate(candidate)
        and _num(getattr(candidate, 'confidence', None)) >= _num(os.getenv('QUALITY_STAGE_GATE_MIN_CONFIDENCE'), 68.0)
        and _num(getattr(candidate, 'ev_pct', None)) >= _num(os.getenv('QUALITY_STAGE_GATE_MIN_EV_PCT'), 2.0)
        and _num(getattr(candidate, 'edge_pct', None)) >= _num(os.getenv('QUALITY_STAGE_GATE_MIN_EDGE_PP'), 1.5)
        and int(_num(getattr(candidate, 'books_count', None), 0.0)) >= int(_num(os.getenv('QUALITY_STAGE_GATE_MIN_BOOKS'), 1.0))
    )


def _candidate_key(candidate: Any) -> tuple[Any, Any, Any, Any, Any]:
    return (
        getattr(candidate, 'match_key', None),
        getattr(candidate, 'family', None),
        getattr(candidate, 'selection_key', None),
        getattr(candidate, 'point', None),
        getattr(candidate, 'team_side', None),
    )


def _rank(candidate: Any) -> tuple[float, float, float]:
    return (
        _num(getattr(candidate, 'ev_pct', None), -999.0),
        _num(getattr(candidate, 'edge_pct', None), -999.0),
        _num(getattr(candidate, 'confidence', None), 0.0),
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

    def _real_context_sources(values: set[str]) -> set[str]:
        normalized = {coverage_contract.normalize_source(item) for item in values}
        return {item for item in normalized if item and item not in coverage_contract.AGGREGATE_CONTEXT_SOURCES and item not in NON_MATCH_CONTEXT_SOURCES}

    def context_sources_with_index(candidate: Any) -> set[str]:
        original_sources = _real_context_sources(set(original(candidate) or set()))
        indexed_sources: set[str] = set()
        context_index = _load_context_index()
        inventory_index = _load_inventory_context_index()
        for key in _candidate_context_keys(candidate):
            for source_index in (context_index, inventory_index):
                found = source_index.get(key)
                if isinstance(found, list):
                    indexed_sources.update(str(item) for item in found if str(item).strip())
        merged = set(original_sources) | _real_context_sources(indexed_sources)
        if merged:
            try:
                summary = getattr(candidate, 'source_summary', {}) or {}
                summary['context_index_bridge_sources'] = sorted(_real_context_sources(indexed_sources))
                summary['context_index_bridge_keys'] = _candidate_context_keys(candidate)
                summary['inventory_context_fallback_used'] = 'inventory_context' in _real_context_sources(indexed_sources)
                candidate.source_summary = summary
            except Exception:
                pass
        return merged

    coverage_contract.context_sources_for_candidate = context_sources_with_index
    coverage_contract._harizon_context_index_bridge_patch = True


def _install_rescue_append_bridge() -> None:
    if not _on('POST_INTEGRITY_RESCUE_APPEND_TO_EXISTING_CANDIDATES', True):
        return
    from app.services import model
    from app.services import controlled_candidate_rescue

    factory = getattr(model, 'CandidateFactory', None)
    build_rescue = getattr(controlled_candidate_rescue, '_build_rescue', None)
    if factory is None or not callable(build_rescue):
        return
    if getattr(factory, '_harizon_native_rescue_append_bridge', False):
        return
    original = getattr(factory, 'build_candidates', None)
    if not callable(original):
        return

    def patched(self: Any, matches: list[Any], offers_by_match: dict[str, Any], contexts_by_match: dict[str, Any], market_signals_by_match: dict[str, dict[str, Any]] | None = None):
        candidates, rejections, debug = original(self, matches, offers_by_match, contexts_by_match, market_signals_by_match)
        if not offers_by_match:
            return candidates, rejections, debug
        if not isinstance(rejections, dict):
            rejections = {}
        try:
            rescue, rescue_debug = build_rescue(self, matches, offers_by_match, contexts_by_match, rejections)
        except Exception:
            return candidates, rejections, debug
        if not rescue:
            return candidates, rejections, debug
        limit = int(_num(os.getenv('POST_INTEGRITY_RESCUE_APPEND_LIMIT'), 24))
        rescue = sorted(list(rescue), key=_rank, reverse=True)[:max(1, limit)]
        seen = {_candidate_key(item) for item in candidates}
        merged = list(candidates)
        appended = 0
        duplicate = 0
        for item in rescue:
            key = _candidate_key(item)
            if key in seen:
                duplicate += 1
                continue
            seen.add(key)
            try:
                item.reasons = list(getattr(item, 'reasons', []) or []) + ['native_rescue_append_bridge']
                if isinstance(getattr(item, 'source_summary', None), dict):
                    item.source_summary['native_rescue_append_bridge'] = True
            except Exception:
                pass
            merged.append(item)
            appended += 1
        debug = dict(debug or {})
        debug['native_rescue_append_bridge'] = {'built': len(rescue), 'appended': appended, 'duplicate': duplicate, 'input_candidates': len(candidates), 'output_candidates': len(merged)}
        try:
            rejections['native_rescue_append_bridge_appended'] = int(rejections.get('native_rescue_append_bridge_appended') or 0) + appended
        except Exception:
            pass
        return merged, rejections, debug

    factory.build_candidates = patched
    factory._harizon_native_rescue_append_bridge = True


def _install_rescue_file_append_bridge() -> None:
    try:
        from app.services import rescue_file_append_bridge
        rescue_file_append_bridge.install()
    except Exception:
        pass


def install() -> None:
    if _on('QUALITY_STAGE_GATE_MARKET_BRIDGE_RELIEF_ENABLED', True):
        _install_quality_stage_gate()
    _install_context_index_bridge()
    _install_rescue_append_bridge()
    _install_rescue_file_append_bridge()
