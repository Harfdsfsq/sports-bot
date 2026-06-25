from __future__ import annotations

import json
import os
import re
from dataclasses import fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PATHS = (Path('.data/exports/latest-rescue-candidates.json'), Path('artifacts/run-bot/latest-rescue-candidates.json'))
LINE_HISTORY_PATHS = (Path('.data/line_history/latest.json'), Path('artifacts/run-bot/line_history-latest.json'))


def _on(name: str, default: bool = True) -> bool:
    raw = str(os.getenv(name) or '').strip().lower()
    return default if not raw else raw in {'1', 'true', 'yes', 'on', 'force'}


def _num(value: Any, default: float = 0.0) -> float:
    try:
        return float(str(value).replace(',', '.')) if value not in (None, '') else default
    except Exception:
        return default


def _dt(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if value in (None, ''):
        return None
    try:
        text = str(value).strip()
        if text.endswith('Z'):
            text = text[:-1] + '+00:00'
        parsed = datetime.fromisoformat(text)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _norm(value: Any) -> str:
    return ' '.join(''.join(ch.lower() if ch.isalnum() else ' ' for ch in str(value or '').replace('_', ' ')).split())


def _date(value: Any) -> str:
    text = str(value or '')
    m = re.search(r'(20\d{2}-\d{2}-\d{2})', text)
    return m.group(1) if m else ''


def _match_keys_from_parts(home: Any, away: Any, day: str) -> set[str]:
    h = _norm(home)
    a = _norm(away)
    if not h or not a or not day:
        return set()
    return {f'soccer|{h}|{a}|{day}', f'soccer|{a}|{h}|{day}', f'{day}|{h}|{a}', f'{day}|{a}|{h}'}


def _candidate_context_keys(candidate: Any) -> set[str]:
    raw = str(getattr(candidate, 'match_key', '') or '')
    day = _date(getattr(candidate, 'commence_time', '')) or _date(raw)
    keys = {raw, raw.replace('_', ' ')} if raw else set()
    keys.update(_match_keys_from_parts(getattr(candidate, 'home_team', ''), getattr(candidate, 'away_team', ''), day))
    return {key for key in keys if key}


def _context_value(ctx: Any, key: str, default: Any = None) -> Any:
    if isinstance(ctx, dict):
        return ctx.get(key, default)
    return getattr(ctx, key, default)


def _context_sources_from_value(ctx: Any) -> set[str]:
    sources: set[str] = set()
    source = _context_value(ctx, 'source') or _context_value(ctx, 'provider') or _context_value(ctx, 'context_source')
    if source:
        sources.add(str(source))
    details = _context_value(ctx, 'details')
    if isinstance(details, dict):
        for key in ('context_sources', 'merged_sources', 'sources'):
            value = details.get(key)
            if isinstance(value, (list, tuple, set)):
                sources.update(str(item) for item in value if str(item).strip())
            elif isinstance(value, str) and value.strip():
                sources.add(value.strip())
    return sources


def _context_xg(ctx: Any) -> tuple[float | None, float | None]:
    home = _num(_context_value(ctx, 'expected_home'), None)
    away = _num(_context_value(ctx, 'expected_away'), None)
    if home is not None and away is not None:
        return home, away
    details = _context_value(ctx, 'details')
    if isinstance(details, dict):
        return _num(details.get('expected_home') or details.get('home_xg'), None), _num(details.get('expected_away') or details.get('away_xg'), None)
    return None, None


def _runtime_context_index(contexts_by_match: Any) -> dict[str, list[Any]]:
    if not isinstance(contexts_by_match, dict):
        return {}
    out: dict[str, list[Any]] = {}
    for raw_key, value in contexts_by_match.items():
        items = value if isinstance(value, list) else [value]
        keys = {str(raw_key), str(raw_key).replace('_', ' ')}
        for ctx in items:
            details = _context_value(ctx, 'details')
            observations = details.get('context_observations') if isinstance(details, dict) else None
            if isinstance(observations, list):
                for obs in observations:
                    if isinstance(obs, dict) and obs.get('match_key'):
                        keys.add(str(obs.get('match_key')))
        for key in keys:
            if key:
                out.setdefault(key, []).extend(items)
    return out


def _apply_runtime_context(candidate: Any, index: dict[str, list[Any]]) -> bool:
    contexts: list[Any] = []
    for key in _candidate_context_keys(candidate):
        contexts.extend(index.get(key) or [])
    if not contexts:
        return False
    sources: set[str] = set()
    expected_home = expected_away = None
    for ctx in contexts:
        sources.update(_context_sources_from_value(ctx))
        h, a = _context_xg(ctx)
        if h is not None and a is not None and expected_home is None:
            expected_home, expected_away = h, a
    if not sources:
        sources.add('runtime_context')
    try:
        summary = dict(getattr(candidate, 'source_summary', {}) or {})
        existing = summary.get('context_sources')
        merged = set(existing if isinstance(existing, list) else []) | sources
        summary['context_sources'] = sorted(str(item) for item in merged if str(item).strip())
        summary['runtime_context_bridge_sources'] = sorted(str(item) for item in sources if str(item).strip())
        candidate.source_summary = summary
        if expected_home is not None and expected_away is not None:
            if getattr(candidate, 'expected_home', None) is None:
                candidate.expected_home = expected_home
            if getattr(candidate, 'expected_away', None) is None:
                candidate.expected_away = expected_away
    except Exception:
        return False
    return True


def _key(candidate: Any) -> tuple[Any, Any, Any, Any, Any]:
    return (getattr(candidate, 'match_key', None), getattr(candidate, 'family', None), getattr(candidate, 'selection_key', None), getattr(candidate, 'point', None), getattr(candidate, 'team_side', None))


def _rank(candidate: Any) -> tuple[float, float, float]:
    return (_num(getattr(candidate, 'ev_pct', None), -999.0), _num(getattr(candidate, 'edge_pct', None), -999.0), _num(getattr(candidate, 'confidence', None), 0.0))


def _allowed(row: dict[str, Any]) -> bool:
    allowed = {x.strip().lower() for x in str(os.getenv('MAIN_POOL_RESCUE_FILE_ALLOWED_SOURCES') or 'a_cover_market_promotion,b_cover_market_promotion,line_history').split(',') if x.strip()}
    source = str(row.get('_candidate_source') or row.get('candidate_source') or row.get('source') or '').strip().lower()
    summary = row.get('source_summary') if isinstance(row.get('source_summary'), dict) else {}
    selected = str(summary.get('selected_source') or '').strip().lower()
    return source in allowed or selected in allowed


def _selection_key(row: dict[str, Any]) -> str:
    raw = str(row.get('selection_key') or '').strip().lower()
    if raw in {'over', 'under'}:
        point = row.get('point')
        point_text = '' if point in (None, '') else f'{_num(point):g}'
        return '|'.join([str(row.get('family') or 'totals'), raw, point_text, str(row.get('team_side') or '').lower()])
    if raw:
        return raw
    selection = str(row.get('selection') or '').lower()
    side = 'under' if ('under' in selection or 'меньше' in selection or 'тм' in selection) else 'over' if ('over' in selection or 'больше' in selection or 'тб' in selection) else selection
    point = row.get('point')
    point_text = '' if point in (None, '') else f'{_num(point):g}'
    return '|'.join([str(row.get('family') or 'totals'), side, point_text, str(row.get('team_side') or '').lower()])


def _teams_from_match_key(value: Any) -> tuple[str, str]:
    text = str(value or '')
    parts = [p for p in text.split('|') if p]
    if len(parts) >= 3 and re.match(r'^20\d{2}-\d{2}-\d{2}$', parts[0]):
        return parts[1].title(), parts[2].title()
    if len(parts) >= 4 and parts[0] == 'soccer':
        return parts[1].title(), parts[2].title()
    return '', ''


def _line_history_rows(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict) or not isinstance(payload.get('lines'), dict):
        return []
    rows: list[dict[str, Any]] = []
    for item in payload['lines'].values():
        if not isinstance(item, dict):
            continue
        snap = item.get('last_snapshot')
        if not isinstance(snap, dict):
            snaps = item.get('snapshots')
            snap = snaps[-1] if isinstance(snaps, list) and snaps and isinstance(snaps[-1], dict) else None
        if not isinstance(snap, dict):
            continue
        row = dict(snap)
        row['_candidate_source'] = str(row.get('source') or 'line_history')
        row.setdefault('commence_time', row.get('kickoff_utc'))
        row.setdefault('odds', row.get('price') or row.get('odds'))
        row.setdefault('edge_pct', row.get('edge_pp'))
        home, away = _teams_from_match_key(row.get('match_key'))
        row.setdefault('home_team', home)
        row.setdefault('away_team', away)
        row.setdefault('league_name', 'line_history')
        rows.append(row)
    return rows


def _coerce(row: dict[str, Any]) -> Any | None:
    if not isinstance(row, dict) or not _allowed(row):
        return None
    try:
        from app.schemas import CandidateBet
    except Exception:
        return None
    kickoff = _dt(row.get('commence_time') or row.get('kickoff_utc') or row.get('start_time'))
    odds = _num(row.get('odds') or row.get('selected_odds') or row.get('price'), 0.0)
    if kickoff is None or odds <= 1.0:
        return None
    implied = _num(row.get('implied_probability'), 0.0) or 1.0 / odds
    model_prob = _num(row.get('adjusted_probability'), 0.0) or _num(row.get('final_probability'), 0.0) or _num(row.get('model_probability'), 0.0) or max(0.01, min(0.99, implied + _num(row.get('edge_pct') or row.get('edge_pp'), 0.0) / 100.0))
    market_prob = _num(row.get('market_probability'), 0.0) or _num(row.get('consensus_probability'), 0.0) or implied
    ev = _num(row.get('ev_pct'), (model_prob * odds - 1.0) * 100.0)
    edge = _num(row.get('edge_pct') or row.get('edge_pp'), (model_prob - implied) * 100.0)
    home, away = str(row.get('home_team') or row.get('home') or ''), str(row.get('away_team') or row.get('away') or '')
    if not home or not away:
        home, away = _teams_from_match_key(row.get('match_key'))
    data = {
        'match_key': str(row.get('match_key') or ''), 'sport_key': str(row.get('sport_key') or 'soccer'),
        'league_name': str(row.get('league_name') or row.get('league') or 'line_history'), 'home_team': home, 'away_team': away,
        'commence_time': kickoff, 'family': str(row.get('family') or 'totals'), 'selection': str(row.get('selection') or row.get('selection_key') or ''), 'selection_key': _selection_key(row),
        'odds': odds, 'fair_odds': _num(row.get('fair_odds'), 1.0 / max(model_prob, 0.01)), 'implied_probability': implied,
        'market_probability': market_prob, 'consensus_probability': _num(row.get('consensus_probability'), market_prob), 'model_probability': _num(row.get('model_probability'), model_prob),
        'final_probability': _num(row.get('final_probability'), model_prob), 'adjusted_probability': _num(row.get('adjusted_probability'), model_prob),
        'edge_pct': edge, 'ev_pct': ev, 'confidence': _num(row.get('confidence'), 68.0), 'books_count': int(_num(row.get('books_count'), 1)), 'sources_count': int(_num(row.get('sources_count') or row.get('odds_sources_count'), 1)),
        'model_mode': str(row.get('model_mode') or 'rescue_file_append_bridge'), 'point': row.get('point'), 'expected_home': row.get('expected_home'), 'expected_away': row.get('expected_away'),
        'reasons': list(row.get('reasons') or []) + ['rescue_file_append_bridge'], 'source_summary': dict(row.get('source_summary') or {}), 'bookmaker': row.get('bookmaker') or row.get('selected_bookmaker'),
        'diagnostics': dict(row.get('diagnostics') or {}), 'analysis': dict(row.get('analysis') or {}), 'publication_score': _num(row.get('publication_score'), _num(row.get('confidence'), 68.0) + max(0.0, ev)), 'source_event_id': row.get('source_event_id'), 'team_side': row.get('team_side'), 'raw_bucket_offers': list(row.get('raw_bucket_offers') or row.get('offers') or []),
    }
    names = {field.name for field in fields(CandidateBet)}
    data = {k: v for k, v in data.items() if k in names}
    if not data['match_key'] or not data['home_team'] or not data['away_team'] or not data['selection']:
        return None
    candidate = CandidateBet(**data)
    try:
        candidate.source_summary['rescue_file_append_bridge'] = True
        candidate.source_summary.pop('publish_coverage_contract', None)
        candidate.source_summary.pop('publish_coverage_reasons', None)
        candidate.source_summary.pop('publish_coverage_passed', None)
    except Exception:
        pass
    return candidate


def _load() -> list[Any]:
    rows: list[dict[str, Any]] = []
    for path in PATHS:
        try:
            if path.exists() and path.stat().st_size > 0:
                payload = json.loads(path.read_text(encoding='utf-8'))
                if isinstance(payload, list): rows.extend(x for x in payload if isinstance(x, dict))
                elif isinstance(payload, dict):
                    for key in ('candidates', 'rows', 'items'):
                        value = payload.get(key)
                        if isinstance(value, list): rows.extend(x for x in value if isinstance(x, dict))
        except Exception:
            continue
    if _on('MAIN_POOL_RESCUE_FILE_APPEND_LINE_HISTORY_ENABLED', True):
        for path in LINE_HISTORY_PATHS:
            try:
                if path.exists() and path.stat().st_size > 0:
                    rows.extend(_line_history_rows(json.loads(path.read_text(encoding='utf-8'))))
            except Exception:
                continue
    candidates = [c for c in (_coerce(row) for row in rows) if c is not None]
    candidates.sort(key=_rank, reverse=True)
    return candidates[: max(1, int(_num(os.getenv('MAIN_POOL_RESCUE_FILE_APPEND_LIMIT'), 24)))]


def install() -> None:
    if not _on('MAIN_POOL_RESCUE_FILE_APPEND_ENABLED', True):
        return
    from app.services import model
    from app.services.coverage_contract import sync_candidate_publish_coverage
    factory = getattr(model, 'CandidateFactory', None)
    if factory is None or getattr(factory, '_harizon_rescue_file_append_bridge', False):
        return
    original = getattr(factory, 'build_candidates', None)
    if not callable(original):
        return

    def patched(self: Any, matches: list[Any], offers_by_match: dict[str, Any], contexts_by_match: dict[str, Any], market_signals_by_match: dict[str, dict[str, Any]] | None = None):
        candidates, rejections, debug = original(self, matches, offers_by_match, contexts_by_match, market_signals_by_match)
        file_candidates = _load()
        if not file_candidates:
            return candidates, rejections, debug
        runtime_contexts = _runtime_context_index(contexts_by_match)
        seen = {_key(item) for item in candidates}
        merged = list(candidates)
        appended = duplicate = runtime_context_enriched = 0
        for item in file_candidates:
            if _apply_runtime_context(item, runtime_contexts):
                runtime_context_enriched += 1
            key = _key(item)
            if key in seen:
                duplicate += 1
                continue
            seen.add(key); merged.append(item); appended += 1
        synced = passed = failed = 0
        for item in merged:
            try:
                _apply_runtime_context(item, runtime_contexts)
                decision = sync_candidate_publish_coverage(item, None)
                synced += 1
                if decision.passed:
                    passed += 1
                else:
                    failed += 1
            except Exception:
                failed += 1
        if isinstance(rejections, dict):
            rejections['rescue_file_append_bridge_seen'] = int(rejections.get('rescue_file_append_bridge_seen') or 0) + len(file_candidates)
            rejections['rescue_file_append_bridge_appended'] = int(rejections.get('rescue_file_append_bridge_appended') or 0) + appended
            rejections['rescue_file_append_bridge_coverage_synced'] = int(rejections.get('rescue_file_append_bridge_coverage_synced') or 0) + synced
            rejections['rescue_file_append_bridge_coverage_passed'] = int(rejections.get('rescue_file_append_bridge_coverage_passed') or 0) + passed
            rejections['rescue_file_append_bridge_runtime_context_enriched'] = int(rejections.get('rescue_file_append_bridge_runtime_context_enriched') or 0) + runtime_context_enriched
        debug = dict(debug or {})
        debug['rescue_file_append_bridge'] = {'seen': len(file_candidates), 'appended': appended, 'duplicate': duplicate, 'input_candidates': len(candidates), 'output_candidates': len(merged), 'coverage_synced': synced, 'coverage_passed': passed, 'coverage_failed': failed, 'runtime_context_enriched': runtime_context_enriched}
        return merged, rejections, debug

    factory.build_candidates = patched
    factory._harizon_rescue_file_append_bridge = True
