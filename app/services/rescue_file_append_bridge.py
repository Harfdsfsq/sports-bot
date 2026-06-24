from __future__ import annotations

import json
import os
from dataclasses import fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PATHS = (Path('.data/exports/latest-rescue-candidates.json'), Path('artifacts/run-bot/latest-rescue-candidates.json'))


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


def _key(candidate: Any) -> tuple[Any, Any, Any, Any, Any]:
    return (getattr(candidate, 'match_key', None), getattr(candidate, 'family', None), getattr(candidate, 'selection_key', None), getattr(candidate, 'point', None), getattr(candidate, 'team_side', None))


def _rank(candidate: Any) -> tuple[float, float, float]:
    return (_num(getattr(candidate, 'ev_pct', None), -999.0), _num(getattr(candidate, 'edge_pct', None), -999.0), _num(getattr(candidate, 'confidence', None), 0.0))


def _allowed(row: dict[str, Any]) -> bool:
    allowed = {x.strip().lower() for x in str(os.getenv('MAIN_POOL_RESCUE_FILE_ALLOWED_SOURCES') or 'a_cover_market_promotion,b_cover_market_promotion').split(',') if x.strip()}
    source = str(row.get('_candidate_source') or row.get('candidate_source') or '').strip().lower()
    summary = row.get('source_summary') if isinstance(row.get('source_summary'), dict) else {}
    selected = str(summary.get('selected_source') or '').strip().lower()
    return source in allowed or selected in allowed


def _selection_key(row: dict[str, Any]) -> str:
    if str(row.get('selection_key') or '').strip():
        return str(row.get('selection_key')).strip()
    point = row.get('point')
    point_text = '' if point in (None, '') else f'{_num(point):g}'
    return '|'.join([str(row.get('family') or 'totals'), str(row.get('selection') or '').lower(), point_text, str(row.get('team_side') or '').lower()])


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
    model_prob = _num(row.get('adjusted_probability'), 0.0) or _num(row.get('final_probability'), 0.0) or _num(row.get('model_probability'), 0.0) or max(0.01, min(0.99, implied + _num(row.get('edge_pct'), 0.0) / 100.0))
    market_prob = _num(row.get('market_probability'), 0.0) or _num(row.get('consensus_probability'), 0.0) or implied
    ev = _num(row.get('ev_pct'), (model_prob * odds - 1.0) * 100.0)
    edge = _num(row.get('edge_pct'), (model_prob - implied) * 100.0)
    data = {
        'match_key': str(row.get('match_key') or ''),
        'sport_key': str(row.get('sport_key') or 'soccer'),
        'league_name': str(row.get('league_name') or row.get('league') or ''),
        'home_team': str(row.get('home_team') or row.get('home') or ''),
        'away_team': str(row.get('away_team') or row.get('away') or ''),
        'commence_time': kickoff,
        'family': str(row.get('family') or 'totals'),
        'selection': str(row.get('selection') or ''),
        'selection_key': _selection_key(row),
        'odds': odds,
        'fair_odds': _num(row.get('fair_odds'), 1.0 / max(model_prob, 0.01)),
        'implied_probability': implied,
        'market_probability': market_prob,
        'consensus_probability': _num(row.get('consensus_probability'), market_prob),
        'model_probability': _num(row.get('model_probability'), model_prob),
        'final_probability': _num(row.get('final_probability'), model_prob),
        'adjusted_probability': _num(row.get('adjusted_probability'), model_prob),
        'edge_pct': edge,
        'ev_pct': ev,
        'confidence': _num(row.get('confidence'), 68.0),
        'books_count': int(_num(row.get('books_count'), 1)),
        'sources_count': int(_num(row.get('sources_count') or row.get('odds_sources_count'), 1)),
        'model_mode': str(row.get('model_mode') or 'a_cover_market_promotion'),
        'point': row.get('point'),
        'expected_home': row.get('expected_home'),
        'expected_away': row.get('expected_away'),
        'reasons': list(row.get('reasons') or []) + ['rescue_file_append_bridge'],
        'source_summary': dict(row.get('source_summary') or {}),
        'bookmaker': row.get('bookmaker') or row.get('selected_bookmaker'),
        'diagnostics': dict(row.get('diagnostics') or {}),
        'analysis': dict(row.get('analysis') or {}),
        'publication_score': _num(row.get('publication_score'), _num(row.get('confidence'), 68.0) + max(0.0, ev)),
        'source_event_id': row.get('source_event_id'),
        'team_side': row.get('team_side'),
        'raw_bucket_offers': list(row.get('raw_bucket_offers') or row.get('offers') or []),
    }
    names = {field.name for field in fields(CandidateBet)}
    data = {k: v for k, v in data.items() if k in names}
    if not data['match_key'] or not data['home_team'] or not data['away_team'] or not data['selection']:
        return None
    candidate = CandidateBet(**data)
    try:
        candidate.source_summary['rescue_file_append_bridge'] = True
    except Exception:
        pass
    return candidate


def _load() -> list[Any]:
    rows: list[dict[str, Any]] = []
    for path in PATHS:
        try:
            if path.exists() and path.stat().st_size > 0:
                payload = json.loads(path.read_text(encoding='utf-8'))
                if isinstance(payload, list):
                    rows.extend(x for x in payload if isinstance(x, dict))
                elif isinstance(payload, dict):
                    for key in ('candidates', 'rows', 'items'):
                        value = payload.get(key)
                        if isinstance(value, list):
                            rows.extend(x for x in value if isinstance(x, dict))
        except Exception:
            continue
    candidates = [c for c in (_coerce(row) for row in rows) if c is not None]
    candidates.sort(key=_rank, reverse=True)
    return candidates[: max(1, int(_num(os.getenv('MAIN_POOL_RESCUE_FILE_APPEND_LIMIT'), 24)))]


def install() -> None:
    if not _on('MAIN_POOL_RESCUE_FILE_APPEND_ENABLED', True):
        return
    from app.services import model

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
        seen = {_key(item) for item in candidates}
        merged = list(candidates)
        appended = 0
        duplicate = 0
        for item in file_candidates:
            key = _key(item)
            if key in seen:
                duplicate += 1
                continue
            seen.add(key)
            merged.append(item)
            appended += 1
        if isinstance(rejections, dict):
            rejections['rescue_file_append_bridge_seen'] = int(rejections.get('rescue_file_append_bridge_seen') or 0) + len(file_candidates)
            rejections['rescue_file_append_bridge_appended'] = int(rejections.get('rescue_file_append_bridge_appended') or 0) + appended
        debug = dict(debug or {})
        debug['rescue_file_append_bridge'] = {'seen': len(file_candidates), 'appended': appended, 'duplicate': duplicate, 'input_candidates': len(candidates), 'output_candidates': len(merged)}
        return merged, rejections, debug

    factory.build_candidates = patched
    factory._harizon_rescue_file_append_bridge = True
