from __future__ import annotations

import json
import os
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

EXPORT_DIR = Path('.data/exports')
OUT_JSON = EXPORT_DIR / 'latest-a-cover-candidate-gap-report.json'
UTC = timezone.utc


def load_json(path: Path, default: Any) -> Any:
    try:
        if path.exists() and path.stat().st_size > 0:
            return json.loads(path.read_text(encoding='utf-8', errors='replace'))
    except Exception:
        return default
    return default


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')


def rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if isinstance(payload, dict):
        for key in ('rows', 'matches', 'items', 'data', 'candidates', 'evaluated', 'evaluated_candidates'):
            value = payload.get(key)
            if isinstance(value, list):
                return [x for x in value if isinstance(x, dict)]
    return []


def norm(value: Any) -> str:
    text = str(value or '').strip().lower().replace('ё', 'е')
    text = re.sub(r'[^a-z0-9а-я]+', ' ', text)
    return ' '.join(text.split())


def as_int(value: Any) -> int:
    try:
        if isinstance(value, (list, tuple, set)):
            return len({norm(x) for x in value if norm(x)})
        if isinstance(value, dict):
            return len(value)
        return int(float(value or 0))
    except Exception:
        return 0


def as_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ''):
            return default
        return float(str(value).replace(',', '.'))
    except Exception:
        return default


def parse_dt(value: Any) -> datetime | None:
    try:
        if value in (None, ''):
            return None
        text = str(value).strip()
        if text.endswith('Z'):
            text = text[:-1] + '+00:00'
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)
    except Exception:
        return None


def kickoff(row: dict[str, Any]) -> datetime | None:
    for key in ('commence_time', 'kickoff_utc', 'kickoff', 'start_time'):
        dt = parse_dt(row.get(key))
        if dt is not None:
            return dt
    return None


def count_nested(row: dict[str, Any], *names: str) -> int:
    best = 0
    for src in (row, row.get('coverage'), row.get('metrics'), row.get('metadata'), row.get('source_summary')):
        if not isinstance(src, dict):
            continue
        for name in names:
            best = max(best, as_int(src.get(name)))
    return best


def date_of(row: dict[str, Any]) -> str:
    for key in ('date', 'commence_time', 'kickoff_utc', 'start_time', 'match_key', 'canonical_match_id', 'event_key'):
        match = re.search(r'20\d{2}-\d{2}-\d{2}', str(row.get(key) or ''))
        if match:
            return match.group(0)
    return ''


def home_away(row: dict[str, Any]) -> tuple[str, str]:
    home = norm(row.get('home_team') or row.get('home') or row.get('home_name') or row.get('team_home'))
    away = norm(row.get('away_team') or row.get('away') or row.get('away_name') or row.get('team_away'))
    if home and away:
        return home, away
    raw = str(row.get('match_key') or row.get('canonical_match_id') or row.get('event_key') or '')
    parts = [norm(x) for x in raw.split('|') if norm(x)]
    parts = [x for x in parts if not re.search(r'20\d{2}-\d{2}-\d{2}', x) and x not in {'soccer', 'football'}]
    if len(parts) >= 2:
        return parts[0], parts[1]
    return home, away


def match_key(row: dict[str, Any]) -> str:
    date = date_of(row)
    home, away = home_away(row)
    if home and away:
        return '|'.join([date, home, away])
    return norm(row.get('match_key') or row.get('canonical_match_id') or row.get('event_key'))


def is_a_cover(row: dict[str, Any]) -> bool:
    if str(row.get('tier_a_coverage_ready') or row.get('ready_for_publish')).lower() in {'true', '1', 'yes'}:
        return True
    odds_sources = count_nested(row, 'independent_odds_sources_count', 'odds_sources_count', 'independent_odds_sources', 'odds_sources')
    price_confirmations = count_nested(row, 'price_confirmations', 'price_confirmation_sources_count', 'books_count', 'bookmakers_count', 'same_side_books_max', 'books')
    contexts = count_nested(row, 'context_sources_count', 'confirmation_sources_count', 'context_sources', 'confirmation_sources')
    return odds_sources >= 2 and price_confirmations >= 2 and contexts >= 2


def reason_list(row: dict[str, Any]) -> list[str]:
    result: list[str] = []
    for key in ('reject_reasons', 'reasons', 'hard_reject_reasons', 'failure_reasons'):
        value = row.get(key)
        if isinstance(value, list):
            result.extend(str(x) for x in value if str(x).strip())
        elif isinstance(value, str) and value.strip():
            result.append(value)
    metrics = row.get('metrics') if isinstance(row.get('metrics'), dict) else {}
    for item in metrics.get('quality_reasons') or []:
        result.append('quality_' + str(item))
    return result


def in_future(row: dict[str, Any], now: datetime, min_lead: int) -> bool:
    ko = kickoff(row)
    return ko is not None and ko >= now + timedelta(minutes=max(0, min_lead))


def in_window(row: dict[str, Any], now: datetime, min_lead: int, hours: float) -> bool:
    ko = kickoff(row)
    if ko is None:
        return False
    return now + timedelta(minutes=max(0, min_lead)) <= ko <= now + timedelta(hours=max(0.25, hours))


def main() -> int:
    now = datetime.now(UTC)
    min_lead = as_int(os.getenv('LINE_MOVEMENT_MIN_LEAD_MINUTES') or os.getenv('MIN_KICKOFF_LEAD_MINUTES') or 15)
    window_hours = as_float(os.getenv('CONTROLLED_FALLBACK_PUBLISH_WINDOW_HOURS') or os.getenv('PUBLISH_WINDOW_HOURS') or 2, 2.0)
    inv = rows(load_json(EXPORT_DIR / 'latest-day-inventory-coverage-truth.json', {}))
    evaluated = rows(load_json(EXPORT_DIR / 'latest-controlled-fallback-report.json', {}))
    a_rows = [row for row in inv if is_a_cover(row)]
    active = [row for row in a_rows if in_future(row, now, min_lead)]
    window = [row for row in a_rows if in_window(row, now, min_lead, window_hours)]
    evaluated_by_key: dict[str, list[dict[str, Any]]] = {}
    for row in evaluated:
        evaluated_by_key.setdefault(match_key(row), []).append(row)
    reason_counts = Counter()
    status_counts = Counter()
    samples = []
    for row in active:
        key = match_key(row)
        ev_rows = evaluated_by_key.get(key, [])
        if not ev_rows:
            status = 'active_a_cover_not_selected_by_candidate_layers'
            status_counts[status] += 1
            reason_counts[status] += 1
            reasons = [status]
        else:
            status = 'active_a_cover_seen_in_fallback'
            status_counts[status] += 1
            reasons = []
            for ev_row in ev_rows:
                reasons.extend(reason_list(ev_row))
            if not reasons:
                reasons = ['seen_without_reject_reason']
            reason_counts.update(reasons)
        if len(samples) < 12:
            ko = kickoff(row)
            samples.append({
                'match_key': key,
                'home_team': row.get('home_team') or row.get('home'),
                'away_team': row.get('away_team') or row.get('away'),
                'kickoff_utc': ko.isoformat() if ko else None,
                'status': status,
                'reasons': reasons[:8],
                'odds_sources': count_nested(row, 'independent_odds_sources_count', 'odds_sources_count', 'independent_odds_sources', 'odds_sources'),
                'price_confirmations': count_nested(row, 'price_confirmations', 'price_confirmation_sources_count', 'books_count', 'bookmakers_count', 'same_side_books_max', 'books'),
                'contexts': count_nested(row, 'context_sources_count', 'confirmation_sources_count', 'context_sources', 'confirmation_sources'),
            })
    payload = {
        'created_at_utc': now.isoformat(),
        'status': 'ok',
        'a_cover_rows': len(a_rows),
        'active_future_a_cover_rows': len(active),
        'in_publish_window_a_cover_rows': len(window),
        'fallback_evaluated_rows': len(evaluated),
        'status_counts': dict(status_counts),
        'reason_counts': dict(reason_counts.most_common(20)),
        'samples': samples,
        'plain_explanation': 'If active_a_cover_not_selected_by_candidate_layers is high, coverage is ready but main/fallback candidate selection did not create a candidate for those matches.',
    }
    write_json(OUT_JSON, payload)
    print(json.dumps({'status': 'ok', 'active_future_a_cover_rows': len(active), 'reason_counts': dict(reason_counts.most_common(5))}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
