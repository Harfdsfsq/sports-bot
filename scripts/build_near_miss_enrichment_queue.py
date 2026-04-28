from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path('.').resolve()
UTC = timezone.utc
OUT_DIR = ROOT / '.data' / 'provider_cache' / 'day-shortlist'
EXPORT = ROOT / '.data' / 'exports' / 'latest-near-miss-enrichment-queue.json'


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return default


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')


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


def app_tz() -> ZoneInfo:
    try:
        return ZoneInfo(os.getenv('APP_TIMEZONE') or os.getenv('TZ') or 'Europe/Moscow')
    except Exception:
        return ZoneInfo('Europe/Moscow')


def local_date() -> str:
    explicit = str(os.getenv('DAY_INVENTORY_TARGET_DATE') or '').strip()
    if explicit:
        return explicit
    return datetime.now(UTC).astimezone(app_tz()).date().isoformat()


def parse_dt(value: Any) -> datetime | None:
    if value in (None, ''):
        return None
    try:
        text = str(value).strip()
        if text.endswith('Z'):
            text = text[:-1] + '+00:00'
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)
    except Exception:
        return None


def unwrap(row: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    candidate = row.get('candidate') if isinstance(row.get('candidate'), dict) else row
    metrics = row.get('metrics') if isinstance(row.get('metrics'), dict) else {}
    if not metrics and isinstance(candidate.get('metrics'), dict):
        metrics = candidate.get('metrics')
    reasons = row.get('reject_reasons') or row.get('reasons') or candidate.get('reject_reasons') or candidate.get('reasons') or []
    if isinstance(reasons, str):
        reasons = [reasons]
    return candidate, metrics if isinstance(metrics, dict) else {}, [str(x) for x in reasons if str(x).strip()]


def value_of(candidate: dict[str, Any], metrics: dict[str, Any], *names: str) -> Any:
    for name in names:
        if name in metrics:
            return metrics.get(name)
        if name in candidate:
            return candidate.get(name)
    return None


def source_count(candidate: dict[str, Any], metrics: dict[str, Any]) -> int:
    return as_int(value_of(candidate, metrics, 'sources_count', 'source_count'), 0)


def book_count(candidate: dict[str, Any], metrics: dict[str, Any]) -> int:
    return as_int(value_of(candidate, metrics, 'books_count', 'bookmakers_count', 'bookmaker_count'), 0)


def queue_item(row: dict[str, Any], idx: int, now: datetime, min_ev: float, min_edge: float) -> dict[str, Any] | None:
    candidate, metrics, reasons = unwrap(row)
    match_key = str(candidate.get('match_key') or '').strip()
    if not match_key:
        return None
    kickoff = parse_dt(candidate.get('commence_time') or candidate.get('kickoff_utc') or candidate.get('start_time'))
    if kickoff is None:
        return None
    minutes_to_kickoff = (kickoff - now).total_seconds() / 60.0
    if minutes_to_kickoff <= 20:
        return None
    ev = as_float(value_of(candidate, metrics, 'canonical_ev_pct', 'ev_pct'))
    edge = as_float(value_of(candidate, metrics, 'canonical_edge_pp', 'edge_pp'))
    confidence = as_float(value_of(candidate, metrics, 'confidence'))
    quality = as_float(value_of(candidate, metrics, 'quality_score'))
    sources = source_count(candidate, metrics)
    books = book_count(candidate, metrics)
    if ev < min_ev and edge < min_edge:
        return None
    proxyish = sources <= 1 or books <= 1 or any('proxy' in reason for reason in reasons)
    if not proxyish:
        return None
    family = str(candidate.get('family') or candidate.get('market_family') or '').strip().lower()
    # BTTS is useful as a context target too, but not enough to open publication; keep it in enrichment only.
    needs_confirmation = [reason for reason in reasons if 'proxy' in reason or 'sources_below' in reason or 'books_below' in reason or 'xg_confirmation' in reason]
    priority = (ev * 3.0) + (edge * 4.0) + (confidence * 0.25) + (quality * 0.15)
    if minutes_to_kickoff <= 180:
        priority += 18
    elif minutes_to_kickoff <= 360:
        priority += 10
    if books >= 2:
        priority += 4
    if sources <= 1:
        priority += 8
    return {
        'match_key': match_key,
        'priority': round(priority, 3),
        'kickoff_utc': kickoff.isoformat(),
        'minutes_to_kickoff': round(minutes_to_kickoff, 1),
        'home_team': candidate.get('home_team') or candidate.get('home'),
        'away_team': candidate.get('away_team') or candidate.get('away'),
        'league_name': candidate.get('league_name') or candidate.get('league'),
        'family': family,
        'selection': candidate.get('selection'),
        'point': candidate.get('point'),
        'odds': candidate.get('odds'),
        'ev_pct': round(ev, 3),
        'edge_pp': round(edge, 3),
        'confidence': round(confidence, 3),
        'quality_score': round(quality, 3),
        'books_count': books,
        'sources_count': sources,
        'needs_confirmation_reasons': needs_confirmation[:8],
        'reject_reasons': reasons[:12],
        'queue_source_index': idx,
    }


def main() -> int:
    now = datetime.now(UTC)
    date_key = local_date()
    min_ev = as_float(os.getenv('NEAR_MISS_ENRICHMENT_MIN_EV_PCT'), 8.0)
    min_edge = as_float(os.getenv('NEAR_MISS_ENRICHMENT_MIN_EDGE_PP'), 3.5)
    limit = max(1, as_int(os.getenv('NEAR_MISS_ENRICHMENT_QUEUE_LIMIT'), 40))
    report = load_json(ROOT / 'artifacts' / 'controlled-fallback-report.json', {})
    evaluated = []
    if isinstance(report, dict):
        for key in ('evaluated', 'candidates', 'checked_candidates', 'rejected_candidates'):
            rows = report.get(key)
            if isinstance(rows, list):
                evaluated = [row for row in rows if isinstance(row, dict)]
                break
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for idx, row in enumerate(evaluated):
        item = queue_item(row, idx, now, min_ev, min_edge)
        if item is None:
            continue
        dedupe_key = f"{item['match_key']}|{item.get('family')}|{item.get('selection')}|{item.get('point')}"
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        items.append(item)
    items.sort(key=lambda x: (as_float(x.get('priority')), -as_float(x.get('minutes_to_kickoff'), 99999)), reverse=True)
    items = items[:limit]
    payload = {
        'status': 'ok',
        'created_at_utc': now.isoformat(),
        'date_local': date_key,
        'min_ev_pct': min_ev,
        'min_edge_pp': min_edge,
        'evaluated_seen': len(evaluated),
        'queue_count': len(items),
        'items': items,
        'notes': [
            'Queue contains high-EV near-miss matches that failed mainly because they are proxy/single-source/low-confirmation.',
            'Next run uses this queue to prioritize context/confirmation API calls before generic enrichment.',
        ],
    }
    paths = [
        OUT_DIR / 'latest-near-miss-enrichment-queue.json',
        OUT_DIR / f'{date_key}-near-miss-enrichment-queue.json',
        EXPORT,
    ]
    for path in paths:
        write_json(path, payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
