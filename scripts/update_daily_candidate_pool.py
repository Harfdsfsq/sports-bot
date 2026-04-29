from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

UTC = timezone.utc
ROOT = Path('.').resolve()
EXPORT = ROOT / '.data' / 'exports' / 'latest-daily-candidate-pool.json'


def app_tz() -> ZoneInfo:
    try:
        return ZoneInfo(__import__('os').getenv('APP_TIMEZONE') or __import__('os').getenv('TZ') or 'Europe/Moscow')
    except Exception:
        return ZoneInfo('Europe/Moscow')


def load_json(path: str | Path, default: Any) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except Exception:
        return default


def write_json(path: str | Path, payload: Any) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')


def as_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ''):
            return default
        return float(value)
    except Exception:
        return default


def as_int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ''):
            return default
        return int(float(value))
    except Exception:
        return default


def now_local_date() -> str:
    return datetime.now(UTC).astimezone(app_tz()).date().isoformat()


def first_dict(paths: list[str | Path]) -> dict[str, Any]:
    for path in paths:
        payload = load_json(path, None)
        if isinstance(payload, dict) and payload:
            return payload
    return {}


def fallback_report() -> dict[str, Any]:
    return first_dict([
        ROOT / 'artifacts' / 'controlled-fallback-report.json',
        ROOT / '.data' / 'exports' / 'latest-controlled-fallback-report.json',
    ])


def unwrap(row: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    candidate = row.get('candidate') if isinstance(row.get('candidate'), dict) else row
    metrics = row.get('metrics') if isinstance(row.get('metrics'), dict) else {}
    if not metrics and isinstance(candidate.get('metrics'), dict):
        metrics = candidate.get('metrics')
    reasons = row.get('reject_reasons') or row.get('reasons') or candidate.get('reject_reasons') or candidate.get('reasons') or []
    if isinstance(reasons, str):
        reasons = [reasons]
    return candidate if isinstance(candidate, dict) else {}, metrics if isinstance(metrics, dict) else {}, [str(x) for x in reasons if str(x).strip()]


def metric(candidate: dict[str, Any], metrics: dict[str, Any], *keys: str, default: float = 0.0) -> float:
    for key in keys:
        if key in metrics:
            return as_float(metrics.get(key), default)
        if key in candidate:
            return as_float(candidate.get(key), default)
    return default


def candidate_key(row: dict[str, Any]) -> str:
    parts = [
        str(row.get('match_key') or '').lower(),
        str(row.get('family') or '').lower(),
        str(row.get('selection') or '').lower(),
        str(row.get('point') or '').lower(),
        str(row.get('team_side') or '').lower(),
        str(row.get('commence_time') or '').lower(),
    ]
    key = '|'.join(parts).strip('|')
    if key:
        return key
    return '|'.join([str(row.get('home_team') or ''), str(row.get('away_team') or ''), str(row.get('selection') or ''), str(row.get('odds') or '')]).lower()


def best5_score(candidate: dict[str, Any], metrics: dict[str, Any], reasons: list[str]) -> tuple[float, dict[str, Any]]:
    ev = metric(candidate, metrics, 'canonical_ev_pct', 'ev_pct')
    edge = metric(candidate, metrics, 'canonical_edge_pp', 'edge_pp')
    confidence = metric(candidate, metrics, 'confidence')
    quality = metric(candidate, metrics, 'quality_score', 'quality')
    odds = metric(candidate, metrics, 'odds', default=as_float(candidate.get('odds'), 0.0))
    books = as_int(candidate.get('books_count') or metrics.get('books_count') or candidate.get('bookmakers_count'))
    sources = as_int(candidate.get('sources_count') or metrics.get('sources_count'))
    score = 0.0
    score += max(-20.0, min(30.0, ev * 2.4))
    score += max(-12.0, min(24.0, edge * 4.2))
    score += max(0.0, confidence - 55.0) * 0.7
    score += max(0.0, quality - 58.0) * 0.55
    if 1.55 <= odds <= 2.55:
        score += 4.0
    elif 2.55 < odds <= 3.1:
        score += 1.0
    elif odds > 3.1:
        score -= 4.0
    if books >= 3:
        score += 7.0
    elif books == 2:
        score += 4.0
    elif books == 1:
        score -= 3.5
    if sources >= 2:
        score += 3.0
    penalties = []
    lower = ' | '.join(reasons).lower()
    if 'canonical_negative' in lower or 'negative value' in lower:
        score -= 35.0
        penalties.append('negative_value')
    if 'xg' in lower and ('conflict' in lower or 'outlier' in lower):
        score -= 25.0
        penalties.append('xg_conflict_or_outlier')
    if 'already sent' in lower or 'already' in lower:
        score -= 40.0
        penalties.append('duplicate_or_already_sent')
    if 'started' in lower or 'kickoff' in lower:
        score -= 20.0
        penalties.append('time_window')
    components = {
        'ev_pct': round(ev, 3),
        'edge_pp': round(edge, 3),
        'confidence': round(confidence, 3),
        'quality': round(quality, 3),
        'odds': round(odds, 3),
        'books_count': books,
        'sources_count': sources,
        'penalties': penalties,
    }
    return round(score, 3), components


def evaluated_rows(report: dict[str, Any]) -> list[dict[str, Any]]:
    for key in ('evaluated', 'candidates', 'checked_candidates', 'rejected_candidates'):
        rows = report.get(key)
        if isinstance(rows, list):
            return [row for row in rows if isinstance(row, dict)]
    return []


def current_rows() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    report = fallback_report()
    for row in evaluated_rows(report):
        candidate, metrics, reasons = unwrap(row)
        ev = metric(candidate, metrics, 'canonical_ev_pct', 'ev_pct')
        edge = metric(candidate, metrics, 'canonical_edge_pp', 'edge_pp')
        if ev <= 0 and edge <= 0:
            continue
        score, components = best5_score(candidate, metrics, reasons)
        normalized = {
            'key': candidate_key(candidate),
            'updated_at_utc': datetime.now(UTC).isoformat(),
            'status': 'watch',
            'best5_score': score,
            'score_components': components,
            'match_key': candidate.get('match_key'),
            'home_team': candidate.get('home_team') or candidate.get('home'),
            'away_team': candidate.get('away_team') or candidate.get('away'),
            'league': candidate.get('league') or candidate.get('sport_title'),
            'commence_time': candidate.get('commence_time') or candidate.get('start_time') or candidate.get('kickoff'),
            'family': candidate.get('family'),
            'selection': candidate.get('selection'),
            'point': candidate.get('point'),
            'team_side': candidate.get('team_side'),
            'odds': candidate.get('odds') or metrics.get('odds'),
            'reject_reasons': reasons[:12],
            'source_summary': candidate.get('source_summary') if isinstance(candidate.get('source_summary'), dict) else {},
        }
        if score >= 55 and not components['penalties']:
            normalized['status'] = 'best5_candidate'
        out.append(normalized)
    return out


def main() -> int:
    today = now_local_date()
    pool_path = ROOT / '.data' / 'day_candidates' / f'{today}.json'
    existing = load_json(pool_path, {})
    rows_by_key: dict[str, dict[str, Any]] = {}
    if isinstance(existing, dict):
        for row in existing.get('candidates') or []:
            if isinstance(row, dict) and row.get('key'):
                rows_by_key[str(row['key'])] = row
    for row in current_rows():
        key = str(row.get('key') or '')
        if not key:
            continue
        old = rows_by_key.get(key)
        if not old or as_float(row.get('best5_score')) >= as_float(old.get('best5_score')):
            rows_by_key[key] = row
        else:
            old['updated_at_utc'] = row.get('updated_at_utc')
            rows_by_key[key] = old
    candidates = list(rows_by_key.values())
    candidates.sort(key=lambda x: as_float(x.get('best5_score')), reverse=True)
    payload = {
        'created_at_utc': datetime.now(UTC).isoformat(),
        'date_local': today,
        'policy_version': 'daily-candidate-pool-v1',
        'count': len(candidates),
        'best5_candidates': [row for row in candidates if str(row.get('status')) == 'best5_candidate'][:10],
        'top_watchlist': candidates[:25],
        'candidates': candidates[:300],
        'strategy': 'Persistent day pool for Best5 ranking. It accumulates positive EV/edge near-misses across every run and keeps the best score per candidate key.',
    }
    write_json(pool_path, payload)
    write_json(EXPORT, payload)
    print(json.dumps({'status': 'ok', 'date_local': today, 'count': len(candidates), 'best5_candidates': len(payload['best5_candidates'])}, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
