from __future__ import annotations

"""Build A-tier funnel diagnostics.

Diagnostic-only. It explains why A-tier coverage does not become A-tier
publication by comparing A-cover inventory rows with raw candidates, fallback
candidates and publishable outputs. It separates full-day coverage from active
future/in-window coverage so old inventory rows do not make A-tier look healthier
than it is late in the day.
"""

import json
import os
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path('.').resolve()
EXPORT = ROOT / '.data' / 'exports'
OUT = EXPORT / 'latest-a-tier-funnel-diagnostics.json'
UTC = timezone.utc


def load(path: Path, default: Any) -> Any:
    try:
        if path.exists() and path.stat().st_size > 0:
            return json.loads(path.read_text(encoding='utf-8', errors='replace'))
    except Exception:
        return default
    return default


def dump(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')


def rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if isinstance(payload, dict):
        for key in ('rows', 'matches', 'items', 'data', 'candidates', 'evaluated', 'evaluated_candidates', 'selected_all'):
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


def boolish(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or '').strip().lower() in {'1', 'true', 'yes', 'y', 'on'}


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
    for src in (row, row.get('metadata'), row.get('source_summary'), row.get('coverage'), row.get('metrics')):
        if not isinstance(src, dict):
            continue
        for name in names:
            best = max(best, as_int(src.get(name)))
    return best


def date_of(row: dict[str, Any]) -> str:
    for key in ('date', 'commence_time', 'kickoff_utc', 'start_time', 'match_key', 'canonical_match_id', 'event_key'):
        m = re.search(r'20\d{2}-\d{2}-\d{2}', str(row.get(key) or ''))
        if m:
            return m.group(0)
    return ''


def home_away(row: dict[str, Any]) -> tuple[str, str]:
    home = norm(row.get('home_team') or row.get('home') or row.get('home_name') or row.get('team_home'))
    away = norm(row.get('away_team') or row.get('away') or row.get('away_name') or row.get('team_away'))
    if home and away:
        return home, away
    raw = str(row.get('match_key') or row.get('canonical_match_id') or row.get('event_key') or '')
    parts = [norm(x) for x in raw.split('|') if norm(x)]
    text_parts = [p for p in parts if not re.search(r'20\d{2}-\d{2}-\d{2}', p) and p not in {'soccer', 'football', 'teams'}]
    if len(text_parts) >= 2:
        return text_parts[0], text_parts[1]
    return home, away


def match_key(row: dict[str, Any]) -> str:
    date = date_of(row)
    league = norm(row.get('league_name') or row.get('league') or row.get('competition'))
    home, away = home_away(row)
    if home and away:
        return '|'.join([date, league, home, away])
    return norm(row.get('match_key') or row.get('canonical_match_id') or row.get('event_key'))


def reason_list(row: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for key in ('reject_reasons', 'reasons', 'hard_reject_reasons', 'failure_reasons'):
        value = row.get(key)
        if isinstance(value, list):
            out.extend(str(x) for x in value if str(x).strip())
        elif isinstance(value, str) and value.strip():
            out.append(value)
    return out


def odds_source_count(row: dict[str, Any]) -> int:
    return count_nested(row, 'independent_odds_sources_count', 'odds_sources_count', 'sources_count', 'independent_odds_sources', 'odds_sources', 'sources')


def price_confirmation_count(row: dict[str, Any]) -> int:
    return count_nested(row, 'price_confirmations', 'price_confirmation_sources_count', 'price_sources_count', 'books_count', 'bookmakers_count', 'same_side_books_max', 'books', 'bookmakers')


def context_count(row: dict[str, Any]) -> int:
    return count_nested(row, 'context_sources_count', 'confirmation_sources_count', 'context_count', 'context_sources', 'confirmation_sources', 'context_confirmations')


def is_a_cover(row: dict[str, Any]) -> bool:
    if boolish(row.get('tier_a_coverage_ready')) or boolish(row.get('ready_for_publish')):
        return True
    return odds_source_count(row) >= 2 and price_confirmation_count(row) >= 2 and context_count(row) >= 2


def future_rows(items: list[dict[str, Any]], now: datetime, *, min_lead_min: int = 0) -> list[dict[str, Any]]:
    cutoff = now + timedelta(minutes=max(0, min_lead_min))
    out = []
    for row in items:
        ko = kickoff(row)
        if ko is not None and ko >= cutoff:
            out.append(row)
    return out


def in_publish_window_rows(items: list[dict[str, Any]], now: datetime, *, min_lead_min: int, window_hours: float) -> list[dict[str, Any]]:
    earliest = now + timedelta(minutes=max(0, min_lead_min))
    latest = now + timedelta(hours=max(0.25, window_hours))
    out = []
    for row in items:
        ko = kickoff(row)
        if ko is not None and earliest <= ko <= latest:
            out.append(row)
    return out


def count_overlap(left: list[dict[str, Any]], right_keys: set[str]) -> int:
    return len({match_key(row) for row in left if match_key(row)} & right_keys)


def main() -> int:
    now = datetime.now(UTC)
    min_lead = as_int(os.getenv('LINE_MOVEMENT_MIN_LEAD_MINUTES') or os.getenv('MIN_KICKOFF_LEAD_MINUTES') or 15)
    publish_window_hours = as_float(os.getenv('CONTROLLED_FALLBACK_PUBLISH_WINDOW_HOURS') or os.getenv('PUBLISH_WINDOW_HOURS') or 2.0, 2.0)

    inv_payload = load(EXPORT / 'latest-day-inventory-coverage-truth.json', {})
    inv = rows(inv_payload)
    counts = inv_payload.get('counts') if isinstance(inv_payload, dict) and isinstance(inv_payload.get('counts'), dict) else {}
    a_cover_rows = [row for row in inv if is_a_cover(row)]
    active_a_cover_rows = future_rows(a_cover_rows, now, min_lead_min=min_lead)
    in_window_a_cover_rows = in_publish_window_rows(a_cover_rows, now, min_lead_min=min_lead, window_hours=publish_window_hours)
    a_keys = {match_key(row) for row in a_cover_rows if match_key(row)}
    active_a_keys = {match_key(row) for row in active_a_cover_rows if match_key(row)}
    in_window_a_keys = {match_key(row) for row in in_window_a_cover_rows if match_key(row)}

    raw_candidates = rows(load(EXPORT / 'latest-debug-candidates-before-quality.json', {}))
    quality_report = load(EXPORT / 'latest-quality-report.json', {})
    picks = rows(load(EXPORT / 'latest-picks.json', {}))
    fallback_report = load(EXPORT / 'latest-controlled-fallback-report.json', {})
    evaluated = rows(fallback_report)

    raw_keys = {match_key(row) for row in raw_candidates if match_key(row)}
    pick_keys = {match_key(row) for row in picks if match_key(row)}
    evaluated_keys = {match_key(row) for row in evaluated if match_key(row)}

    raw_reason_counter = Counter()
    fallback_reason_counter = Counter()
    quality_sources = Counter()
    for row in raw_candidates:
        raw_reason_counter.update(reason_list(row))
        metrics = row.get('metrics') if isinstance(row.get('metrics'), dict) else {}
        quality_sources[str(metrics.get('quality_score_source') or row.get('quality_score_source') or 'unknown')] += 1
    for row in evaluated:
        fallback_reason_counter.update(reason_list(row))
        metrics = row.get('metrics') if isinstance(row.get('metrics'), dict) else {}
        quality_sources[str(metrics.get('quality_score_source') or row.get('quality_score_source') or 'unknown')] += 1

    missing_raw_samples = []
    for row in active_a_cover_rows or a_cover_rows:
        key = match_key(row)
        if key in raw_keys:
            continue
        home, away = home_away(row)
        missing_raw_samples.append({
            'match_key': key,
            'home_team': row.get('home_team') or row.get('home') or home,
            'away_team': row.get('away_team') or row.get('away') or away,
            'league_name': row.get('league_name') or row.get('league'),
            'kickoff': row.get('commence_time') or row.get('kickoff_utc') or row.get('start_time'),
            'odds_sources': odds_source_count(row),
            'price_confirmations': price_confirmation_count(row),
            'contexts': context_count(row),
        })
        if len(missing_raw_samples) >= 12:
            break

    payload = {
        'created_at_utc': now.isoformat(),
        'status': 'ok',
        'min_lead_minutes': min_lead,
        'publish_window_hours': publish_window_hours,
        'a_cover_rows': len(a_cover_rows),
        'active_future_a_cover_rows': len(active_a_cover_rows),
        'in_publish_window_a_cover_rows': len(in_window_a_cover_rows),
        'coverage_truth_matches_ready_for_publish': as_int(counts.get('matches_ready_for_publish')),
        'raw_candidates_before_quality': len(raw_candidates),
        'a_cover_with_raw_candidate': len(a_keys & raw_keys),
        'a_cover_without_raw_candidate': max(0, len(a_keys - raw_keys)),
        'active_a_cover_with_raw_candidate': len(active_a_keys & raw_keys),
        'active_a_cover_without_raw_candidate': max(0, len(active_a_keys - raw_keys)),
        'in_window_a_cover_with_raw_candidate': len(in_window_a_keys & raw_keys),
        'in_window_a_cover_without_raw_candidate': max(0, len(in_window_a_keys - raw_keys)),
        'fallback_evaluated_rows': len(evaluated),
        'a_cover_seen_in_fallback': len(a_keys & evaluated_keys),
        'active_a_cover_seen_in_fallback': len(active_a_keys & evaluated_keys),
        'in_window_a_cover_seen_in_fallback': len(in_window_a_keys & evaluated_keys),
        'published_pick_rows': len(picks),
        'a_cover_published_rows': len(a_keys & pick_keys),
        'quality_report_status': quality_report.get('status') if isinstance(quality_report, dict) else None,
        'quality_report_summary': quality_report.get('summary') if isinstance(quality_report, dict) else None,
        'raw_candidate_reason_counts': dict(raw_reason_counter),
        'fallback_reason_counts': dict(fallback_reason_counter),
        'quality_score_sources': dict(quality_sources),
        'missing_raw_candidate_samples': missing_raw_samples,
        'plain_explanation': [
            'A-cover is full-day evidence coverage. Active future/in-window A-cover is the actionable subset for the current run.',
            'A-cover becomes A-tier publication only if raw candidate generation, value, xG, quality, movement and final publish guards also pass.',
            'If active_a_cover_without_raw_candidate is high, the loss happens before tier checks: candidate factory/model does not create a candidate for active A-cover matches.',
        ],
    }
    dump(OUT, payload)
    print(json.dumps({'status': 'ok', 'a_cover_rows': payload['a_cover_rows'], 'active_future_a_cover_rows': payload['active_future_a_cover_rows'], 'in_publish_window_a_cover_rows': payload['in_publish_window_a_cover_rows'], 'active_a_cover_with_raw_candidate': payload['active_a_cover_with_raw_candidate']}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
