from __future__ import annotations

"""Trim oversized cached day inventory for fast runs.

The normal builder can temporarily accumulate far more than the publication target
when cached provider evidence is merged.  For fast runs we keep the most relevant
upcoming matches first while preserving matches with odds/context evidence.
"""

import json
import os
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

UTC = timezone.utc
ROOT = Path('.')
DAY_DIR = ROOT / '.data/day_inventory'
EXPORT_DIR = ROOT / '.data/exports'
REPORT_PATH = EXPORT_DIR / 'latest-fast-inventory-trim.json'


def truthy(value: Any, default: bool = False) -> bool:
    raw = str(value if value is not None else '').strip().lower()
    if not raw:
        return default
    return raw in {'1', 'true', 'yes', 'on', 'fast'}


def as_int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ''):
            return default
        return int(float(str(value)))
    except Exception:
        return default


def parse_dt(value: Any) -> datetime | None:
    if value in (None, ''):
        return None
    text = str(value).strip()
    try:
        if text.endswith('Z'):
            text = text[:-1] + '+00:00'
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)
    except Exception:
        return None


def app_date() -> str:
    explicit = str(os.getenv('DAY_INVENTORY_TARGET_DATE') or '').strip()
    if explicit:
        return explicit
    try:
        tz = ZoneInfo(os.getenv('APP_TIMEZONE') or os.getenv('TZ') or 'Europe/Moscow')
    except Exception:
        tz = ZoneInfo('Europe/Moscow')
    return datetime.now(UTC).astimezone(tz).date().isoformat()


def load_json(path: Path, default: Any) -> Any:
    try:
        if path.exists() and path.stat().st_size > 0:
            return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        pass
    return default


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')


def seq_len(value: Any) -> int:
    if isinstance(value, (list, tuple, set)):
        return len([x for x in value if str(x or '').strip()])
    if isinstance(value, dict):
        return len(value)
    if isinstance(value, str):
        return len([x for x in re.split(r'[,|;/]+', value) if x.strip()])
    return 0


def evidence_score(row: dict[str, Any]) -> int:
    cov = row.get('coverage') if isinstance(row.get('coverage'), dict) else {}
    meta = row.get('metadata') if isinstance(row.get('metadata'), dict) else {}
    odds_sources = seq_len(row.get('odds_sources')) + seq_len(row.get('line_sources'))
    context_sources = seq_len(row.get('context_sources')) + seq_len(row.get('context_confirmations'))
    books = max(as_int(row.get('books_count')), as_int(meta.get('books_count')), seq_len(row.get('books')))
    price = max(as_int(row.get('price_confirmation_sources_count')), as_int(meta.get('price_confirmation_sources_count')), books)
    score = 0
    score += 40 if cov.get('odds') or odds_sources or price else 0
    score += 35 if cov.get('context') or context_sources else 0
    score += min(20, price * 5)
    score += min(20, odds_sources * 8)
    score += min(20, context_sources * 5)
    score += 12 if cov.get('ready_for_model') else 0
    return score


def kickoff_value(row: dict[str, Any]) -> datetime | None:
    for key in ('kickoff_utc', 'commence_time', 'start_time', 'kickoff'):
        dt = parse_dt(row.get(key))
        if dt is not None:
            return dt
    return None


def rank_row(row: dict[str, Any], now: datetime, keep_until: datetime) -> tuple[int, int, float, str]:
    kickoff = kickoff_value(row)
    ev = evidence_score(row)
    if kickoff is None:
        bucket = 5
        minutes = 999999.0
    else:
        minutes = (kickoff - now).total_seconds() / 60.0
        if minutes < -15:
            bucket = 6
        elif kickoff <= keep_until:
            bucket = 0
        elif kickoff <= now + timedelta(hours=24):
            bucket = 1
        else:
            bucket = 2
    # Lower tuple is better. Evidence is inverted so richer rows stay in the cut.
    name = f"{row.get('league_name','')}|{row.get('home_team','')}|{row.get('away_team','')}"
    return (bucket, -ev, minutes, name)


def main() -> int:
    fast = truthy(os.getenv('HARIZON_FAST_RUN'), False) or str(os.getenv('RUN_MODE') or '').lower() == 'fast'
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    if not fast or not truthy(os.getenv('FAST_RUN_TRIM_DAY_INVENTORY'), True):
        write_json(REPORT_PATH, {'created_at_utc': datetime.now(UTC).isoformat(), 'status': 'disabled', 'fast_run': fast})
        return 0
    limit = max(80, as_int(os.getenv('FAST_RUN_INVENTORY_LIMIT') or os.getenv('DAY_INVENTORY_MAX_MATCHES'), 300))
    window_hours = max(4, as_int(os.getenv('FAST_RUN_KEEP_WINDOW_HOURS') or os.getenv('PUBLISH_WINDOW_HOURS'), 12))
    date = app_date()
    path = DAY_DIR / f'{date}.json'
    payload = load_json(path, {})
    matches = payload.get('matches') if isinstance(payload, dict) and isinstance(payload.get('matches'), list) else []
    before = len(matches)
    now = datetime.now(UTC)
    keep_until = now + timedelta(hours=window_hours)
    if before > limit:
        ranked = sorted([m for m in matches if isinstance(m, dict)], key=lambda row: rank_row(row, now, keep_until))
        payload['matches'] = ranked[:limit]
        payload.setdefault('fast_trim', {})
        payload['fast_trim'].update({'enabled': True, 'before': before, 'after': len(payload['matches']), 'limit': limit, 'window_hours': window_hours, 'trimmed_at_utc': now.isoformat()})
        write_json(path, payload)
        for alias in ('latest.json', 'current.json', 'today.json'):
            write_json(DAY_DIR / alias, payload)
        status = 'trimmed'
    else:
        status = 'not_needed'
    report = {'created_at_utc': now.isoformat(), 'status': status, 'target_date': date, 'before': before, 'after': len(payload.get('matches') or matches), 'limit': limit, 'window_hours': window_hours, 'inventory_path': str(path)}
    write_json(REPORT_PATH, report)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
