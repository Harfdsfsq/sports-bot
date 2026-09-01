from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

OUT = Path('.data/exports/latest-controlled-fallback-confirmation-bridge.json')
INDEX_PATHS = [Path('.data/exports/latest-context-source-index.json'), Path('.data/provider_cache/context-source-index/latest.json')]
CONTEXT_PROVIDERS = {'sstats','bzzoiro','football_data','thesportsdb','espn','clubelo','openfootball','sportlogic','weather','weatherapi','openweathermap','highlightly','futrixmetrics','scorebat','wikidata'}


def _load(path: Path, default: Any) -> Any:
    try:
        if path.exists() and path.stat().st_size > 0:
            return json.loads(path.read_text(encoding='utf-8', errors='replace'))
    except Exception:
        pass
    return default


def _write(payload: dict[str, Any]) -> None:
    try:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    except Exception:
        pass


def _norm(value: Any) -> str:
    text = str(value or '').strip().lower().replace('-', '_').replace(' ', '_')
    aliases = {'football_data_org':'football_data','sportsdb':'thesportsdb','openweathermap':'weather','weatherapi':'weather'}
    if text in aliases:
        return aliases[text]
    for src in CONTEXT_PROVIDERS:
        if src in text:
            return src
    return text


def _split(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(x) for x in value]
    if isinstance(value, str):
        return [x for x in re.split(r'[,|;/+\s]+', value) if x]
    if isinstance(value, dict):
        out: list[str] = []
        for k, v in value.items():
            if v not in (None, '', [], {}, False):
                out.append(str(k))
            out.extend(_split(v))
        return out
    return []


def _candidate_key(candidate: dict[str, Any]) -> str:
    return str(candidate.get('match_key') or candidate.get('canonical_match_id') or '').strip().lower()


def _index_sources(match_key: str) -> set[str]:
    if not match_key:
        return set()
    for path in INDEX_PATHS:
        payload = _load(path, {})
        by_match = payload.get('by_match') if isinstance(payload, dict) else {}
        if isinstance(by_match, dict):
            found = by_match.get(match_key) or by_match.get(match_key.lower())
            if isinstance(found, list):
                return {_norm(x) for x in found if _norm(x) in CONTEXT_PROVIDERS}
    return set()


def _candidate_sources(candidate: dict[str, Any], metrics: dict[str, Any]) -> set[str]:
    out: set[str] = set()
    containers = [candidate, metrics]
    for key in ('source_summary','metadata','coverage','day_inventory_coverage','context'):
        if isinstance(candidate.get(key), dict):
            containers.append(candidate[key])
        if isinstance(metrics.get(key), dict):
            containers.append(metrics[key])
    for box in containers:
        if not isinstance(box, dict):
            continue
        for key in ('confirmation_sources','context_sources','context_source_names','merged_context_sources','providers','provider_names','core_context_sources','verified_context_sources'):
            for item in _split(box.get(key)):
                src = _norm(item)
                if src in CONTEXT_PROVIDERS:
                    out.add(src)
        for key, value in box.items():
            src = _norm(key)
            if src in CONTEXT_PROVIDERS and value not in (None, '', [], {}, False):
                out.add(src)
    out |= _index_sources(_candidate_key(candidate))
    out.discard('odds_api_io')
    out.discard('market')
    return out


def _install_metrics_patch(base: Any, report: dict[str, Any]) -> None:
    for name in ('candidate_metrics', 'metrics_for_candidate', 'build_metrics', 'evaluate_candidate'):
        fn = getattr(base, name, None)
        if not callable(fn) or getattr(fn, '_harizon_confirmation_bridge', False):
            continue
        def wrapped(candidate: dict[str, Any], *args: Any, __fn=fn, **kwargs: Any):
            result = __fn(candidate, *args, **kwargs)
            metrics = result if isinstance(result, dict) else {}
            if isinstance(metrics, dict):
                src = _candidate_sources(candidate, metrics)
                if src:
                    before = int(metrics.get('confirmation_sources_count') or metrics.get('context_sources_count') or metrics.get('sources_count') or 0)
                    merged = sorted(set(_split(metrics.get('confirmation_sources'))) | src)
                    metrics['confirmation_sources'] = merged
                    metrics['context_sources'] = sorted(set(_split(metrics.get('context_sources'))) | src)
                    metrics['confirmation_sources_count'] = max(before, len(merged))
                    metrics['context_sources_count'] = max(int(metrics.get('context_sources_count') or 0), len(merged))
                    metrics['sources_count'] = max(int(metrics.get('sources_count') or 0), len(merged))
                    report['patched_metrics'] += 1
                    if len(report['samples']) < 20:
                        report['samples'].append({'match_key': candidate.get('match_key'), 'home_team': candidate.get('home_team'), 'away_team': candidate.get('away_team'), 'before': before, 'after': len(merged), 'sources': merged})
            return result
        wrapped._harizon_confirmation_bridge = True  # type: ignore[attr-defined]
        setattr(base, name, wrapped)
        report['patched_functions'].append(name)


def _install_hard_reject_patch(base: Any, report: dict[str, Any]) -> None:
    old = getattr(base, 'hard_reject_reasons', None)
    if not callable(old) or getattr(old, '_harizon_confirmation_bridge', False):
        return
    def wrapped(candidate: dict[str, Any], metrics: dict[str, Any], sent_index: dict[str, Any]) -> list[str]:
        src = _candidate_sources(candidate, metrics)
        if src:
            before = int(metrics.get('confirmation_sources_count') or metrics.get('context_sources_count') or metrics.get('sources_count') or 0)
            merged = sorted(set(_split(metrics.get('confirmation_sources'))) | src)
            metrics['confirmation_sources'] = merged
            metrics['context_sources'] = sorted(set(_split(metrics.get('context_sources'))) | src)
            metrics['confirmation_sources_count'] = max(before, len(merged))
            metrics['context_sources_count'] = max(int(metrics.get('context_sources_count') or 0), len(merged))
            metrics['sources_count'] = max(int(metrics.get('sources_count') or 0), len(merged))
            report['patched_hard_reject_inputs'] += 1
        reasons = list(old(candidate, metrics, sent_index) or [])
        count = int(metrics.get('confirmation_sources_count') or metrics.get('sources_count') or 0)
        if count > 0:
            filtered = []
            for r in reasons:
                text = str(r).lower().replace('_', ' ')
                if text == 'missing sources' or text == 'нет подтверждения источниками':
                    report['removed_missing_sources'] += 1
                    continue
                filtered.append(r)
            reasons = filtered
        return reasons
    wrapped._harizon_confirmation_bridge = True  # type: ignore[attr-defined]
    base.hard_reject_reasons = wrapped
    report['patched_functions'].append('hard_reject_reasons')


def install(base: Any) -> dict[str, Any]:
    report = {'status':'installed','created_at_utc':datetime.now(timezone.utc).isoformat(),'patched_functions':[],'patched_metrics':0,'patched_hard_reject_inputs':0,'removed_missing_sources':0,'samples':[],'publication_contract_relaxed':False}
    try:
        _install_metrics_patch(base, report)
        _install_hard_reject_patch(base, report)
    finally:
        _write(report)
    return report

__all__ = ['install']
