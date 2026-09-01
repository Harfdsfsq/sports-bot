from __future__ import annotations

import json
import os
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path('.').resolve()
DAY_DIR = ROOT / '.data' / 'day_inventory'
CACHE_DIR = ROOT / '.data' / 'cache' / 'day_inventory'
EXPORT_DIR = ROOT / '.data' / 'exports'
REPORT_PATH = EXPORT_DIR / 'latest-day-inventory-semantic-dedupe.json'
ALIASES = ('current.json', 'latest.json', 'today.json')


def _tz() -> ZoneInfo:
    try:
        return ZoneInfo(os.getenv('APP_TIMEZONE') or os.getenv('TZ') or 'Europe/Moscow')
    except Exception:
        return ZoneInfo('UTC')


def _target_date() -> str:
    return str(os.getenv('DAY_INVENTORY_TARGET_DATE') or os.getenv('DAY_INVENTORY_CACHE_DATE') or datetime.now(_tz()).date().isoformat())[:10]


def _load(path: Path) -> dict[str, Any]:
    try:
        if path.exists() and path.stat().st_size > 0:
            value = json.loads(path.read_text(encoding='utf-8', errors='replace'))
            return value if isinstance(value, dict) else {}
    except Exception:
        pass
    return {}


def _write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')


def _dt(value: Any) -> datetime | None:
    if value in (None, ''):
        return None
    try:
        text = str(value).strip()
        if text.endswith('Z'):
            text = text[:-1] + '+00:00'
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _date(row: dict[str, Any]) -> str:
    for key in ('kickoff_utc', 'commence_time', 'start_time', 'kickoff', 'date'):
        value = row.get(key)
        if not value:
            continue
        if key == 'date' and re.match(r'^20\d{2}-\d{2}-\d{2}$', str(value)[:10]):
            return str(value)[:10]
        dt = _dt(value)
        if dt:
            return dt.astimezone(_tz()).date().isoformat()
    for key in ('match_key', 'canonical_match_id', 'event_key'):
        m = re.search(r'(20\d{2}-\d{2}-\d{2})', str(row.get(key) or ''))
        if m:
            return m.group(1)
    return ''


def _team(value: Any) -> str:
    text = str(value or '').strip().lower().replace('ё', 'е').replace('´', "'")
    text = re.sub(r'\b(fc|sc|cf|fk|ac|cd|club|de|la|the|w|women|u19|u20|u21|ii|2)\b', ' ', text)
    text = re.sub(r'[^a-z0-9а-я]+', ' ', text)
    return ' '.join(text.split())


def _norm(value: Any) -> str:
    text = str(value or '').strip().lower().replace('ё', 'е')
    text = re.sub(r'[^a-z0-9а-я]+', ' ', text)
    return ' '.join(text.split())


def _home(row: dict[str, Any]) -> str:
    return str(row.get('home_team') or row.get('home') or row.get('home_name') or row.get('team_home') or '').strip()


def _away(row: dict[str, Any]) -> str:
    return str(row.get('away_team') or row.get('away') or row.get('away_name') or row.get('team_away') or '').strip()


def semantic_key(row: dict[str, Any]) -> str:
    day = _date(row)
    home = _team(_home(row))
    away = _team(_away(row))
    if day and home and away:
        return f'{day}|{home}|{away}'
    raw = str(row.get('canonical_match_id') or row.get('match_key') or row.get('event_key') or row.get('id') or '').strip()
    return _norm(raw)


def _list(v: Any) -> list[Any]:
    if isinstance(v, list):
        return list(v)
    if isinstance(v, (tuple, set)):
        return list(v)
    if isinstance(v, dict):
        return list(v.keys())
    if v not in (None, '', False):
        return [v]
    return []


def _richness(row: dict[str, Any]) -> int:
    score = 0
    for obj in (row, row.get('coverage') if isinstance(row.get('coverage'), dict) else {}, row.get('source_summary') if isinstance(row.get('source_summary'), dict) else {}):
        if not isinstance(obj, dict):
            continue
        for key in ('odds_sources', 'context_sources', 'bookmakers', 'books', 'price_confirmations', 'confirmation_sources'):
            score += min(50, len(_list(obj.get(key))))
        for key in ('expected_home', 'expected_away', 'market_probability', 'odds', 'price'):
            if obj.get(key) not in (None, ''):
                score += 2
    for key in ('home_team', 'away_team', 'league_name', 'kickoff_utc', 'commence_time'):
        if row.get(key):
            score += 1
    return score


def merge(old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    base, extra = (dict(new), old) if _richness(new) > _richness(old) else (dict(old), new)
    for key, value in extra.items():
        if key not in base or base.get(key) in (None, '', [], {}):
            base[key] = value
        elif isinstance(base.get(key), dict) and isinstance(value, dict):
            merged = dict(value)
            merged.update(base[key])
            base[key] = merged
        elif isinstance(base.get(key), list) and isinstance(value, list):
            seen = {json.dumps(x, ensure_ascii=False, sort_keys=True, default=str) for x in base[key]}
            for item in value:
                sig = json.dumps(item, ensure_ascii=False, sort_keys=True, default=str)
                if sig not in seen:
                    base[key].append(item)
                    seen.add(sig)
    base.setdefault('canonical_match_id', semantic_key(base))
    return base


def dedupe_rows(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    by_key: dict[str, dict[str, Any]] = {}
    counts: Counter[str] = Counter()
    for row in rows:
        if not isinstance(row, dict):
            continue
        key = semantic_key(row)
        if not key:
            continue
        counts[key] += 1
        by_key[key] = merge(by_key[key], row) if key in by_key else dict(row)
    out = list(by_key.values())
    out.sort(key=lambda r: (_dt(r.get('kickoff_utc') or r.get('commence_time') or r.get('start_time') or r.get('kickoff')) or datetime.max.replace(tzinfo=timezone.utc), -_richness(r), semantic_key(r)))
    duplicates = {k: v for k, v in counts.items() if v > 1}
    return out, {'duplicate_groups': len(duplicates), 'duplicate_rows_removed': sum(v - 1 for v in duplicates.values()), 'largest_duplicate_groups': sorted(duplicates.items(), key=lambda x: x[1], reverse=True)[:25]}


def dedupe_payload(payload: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    rows = payload.get('matches') if isinstance(payload.get('matches'), list) else []
    clean, stats = dedupe_rows([x for x in rows if isinstance(x, dict)])
    out = dict(payload)
    out['matches'] = clean
    counts = out.setdefault('counts', {}) if isinstance(out.setdefault('counts', {}), dict) else {}
    counts['matches_total'] = len(clean)
    counts['semantic_duplicate_rows_removed'] = stats['duplicate_rows_removed']
    out['semantic_dedupe_updated_at_utc'] = datetime.now(timezone.utc).isoformat()
    return out, stats


def main() -> int:
    day = _target_date()
    paths = [DAY_DIR / f'{day}.json', *(DAY_DIR / name for name in ALIASES), CACHE_DIR / f'{day}.json', *(CACHE_DIR / name for name in ALIASES)]
    changed: list[str] = []
    reports: list[dict[str, Any]] = []
    for path in paths:
        payload = _load(path)
        rows = payload.get('matches') if isinstance(payload.get('matches'), list) else []
        if not rows:
            continue
        before = len(rows)
        clean_payload, stats = dedupe_payload(payload)
        after = len(clean_payload.get('matches') or [])
        if after != before or stats.get('duplicate_rows_removed'):
            _write(path, clean_payload)
            changed.append(str(path))
        reports.append({'path': str(path), 'before': before, 'after': after, **stats})
    report = {'created_at_utc': datetime.now(timezone.utc).isoformat(), 'status': 'ok', 'target_date': day, 'changed_paths': changed, 'files': reports}
    _write(REPORT_PATH, report)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
