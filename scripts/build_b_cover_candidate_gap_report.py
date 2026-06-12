from __future__ import annotations

"""Explain why B-covered matches did not become publishable candidates."""

import csv
import json
import os
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

UTC = timezone.utc
ROOT = Path('.').resolve()
EXPORT_DIR = ROOT / '.data' / 'exports'
REPORT_JSON = EXPORT_DIR / 'latest-b-cover-candidate-gap-report.json'
REPORT_CSV = EXPORT_DIR / 'latest-b-cover-candidate-gap-report.csv'


def load_json(path: Path, default: Any = None) -> Any:
    try:
        if path.exists() and path.stat().st_size > 0:
            return json.loads(path.read_text(encoding='utf-8', errors='replace'))
    except Exception:
        return default
    return default


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')


def norm(value: Any) -> str:
    text = str(value or '').strip().lower().replace('ё', 'е')
    text = re.sub(r'[^a-z0-9а-я]+', ' ', text)
    return ' '.join(text.split())


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


def target_date() -> str:
    return (os.getenv('DAY_INVENTORY_TARGET_DATE') or datetime.now(UTC).date().isoformat())[:10]


def row_date(row: dict[str, Any]) -> str:
    for key in ('commence_time', 'kickoff_utc', 'start_time', 'kickoff', 'date'):
        value = row.get(key)
        if not value:
            continue
        if key == 'date' and re.match(r'^20\d{2}-\d{2}-\d{2}$', str(value)[:10]):
            return str(value)[:10]
        dt = parse_dt(value)
        if dt:
            return dt.date().isoformat()
    for key in ('match_key', 'canonical_match_id'):
        m = re.search(r'(20\d{2}-\d{2}-\d{2})', str(row.get(key) or ''))
        if m:
            return m.group(1)
    return ''


def key_variants(row: dict[str, Any]) -> set[str]:
    out: set[str] = set()
    for key in ('canonical_match_id', 'match_key', 'event_key', 'id'):
        value = str(row.get(key) or '').strip()
        if value:
            out.add('id:' + norm(value))
    h = norm(row.get('home_team') or row.get('home'))
    a = norm(row.get('away_team') or row.get('away'))
    d = row_date(row)
    if h and a and d:
        out.add(f'teams:{d}|{h}|{a}')
        out.add(f'teams_rev:{d}|{a}|{h}')
    return out


def count_any(value: Any) -> int:
    if isinstance(value, (list, tuple, set, dict)):
        return len(value)
    try:
        if value in (None, ''):
            return 0
        return int(float(str(value)))
    except Exception:
        return 0


def context_count(row: dict[str, Any]) -> int:
    cov = row.get('coverage') if isinstance(row.get('coverage'), dict) else {}
    md = row.get('metadata') if isinstance(row.get('metadata'), dict) else {}
    best = 0
    for container in (row, cov, md):
        if not isinstance(container, dict):
            continue
        for key in ('context_sources', 'context_confirmations', 'all_context_sources', 'sources'):
            best = max(best, count_any(container.get(key)))
        if container.get('context') or container.get('has_context'):
            best = max(best, 1)
    return best


def book_count(row: dict[str, Any]) -> int:
    cov = row.get('coverage') if isinstance(row.get('coverage'), dict) else {}
    md = row.get('metadata') if isinstance(row.get('metadata'), dict) else {}
    best = 0
    for container in (row, cov, md):
        if not isinstance(container, dict):
            continue
        for key in ('books_count', 'bookmakers_count', 'price_confirmations', 'price_sources_count', 'latest_books_max'):
            best = max(best, count_any(container.get(key)))
        for key in ('books', 'bookmakers'):
            best = max(best, count_any(container.get(key)))
        if container.get('odds') or container.get('has_odds'):
            best = max(best, 1)
    return best


def has_xg(row: dict[str, Any]) -> bool:
    stack = [row]
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            keys = {str(k).lower() for k in item.keys()}
            if {'expected_home', 'expected_away'} <= keys or {'home_xg', 'away_xg'} <= keys or {'xg_home', 'xg_away'} <= keys:
                return True
            stack.extend(item.values())
        elif isinstance(item, list):
            stack.extend(item)
    return False


def load_inventory(day: str) -> list[dict[str, Any]]:
    best: list[dict[str, Any]] = []
    for path in (ROOT / '.data' / 'day_inventory' / f'{day}.json', ROOT / '.data' / 'day_inventory' / 'current.json', ROOT / '.data' / 'day_inventory' / 'latest.json'):
        payload = load_json(path, {})
        rows = payload.get('matches') if isinstance(payload, dict) and isinstance(payload.get('matches'), list) else []
        filtered = [x for x in rows if isinstance(x, dict) and (not row_date(x) or row_date(x) == day)]
        if len(filtered) > len(best):
            best = filtered
    return best


def candidate_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in (ROOT / '.logs' / 'debug-last-run.json', EXPORT_DIR / 'latest-rescue-candidates.json', EXPORT_DIR / 'latest-controlled-fallback-report.json'):
        payload = load_json(path, None)
        if isinstance(payload, dict):
            for key in ('candidates_before_quality', 'candidates_after_quality', 'candidates', 'evaluated', 'selected_all'):
                value = payload.get(key)
                if isinstance(value, list):
                    rows.extend([x for x in value if isinstance(x, dict)])
            selected = payload.get('selected')
            if isinstance(selected, dict):
                rows.append(selected)
        elif isinstance(payload, list):
            rows.extend([x for x in payload if isinstance(x, dict)])
    return rows


def main() -> int:
    day = target_date()
    inventory = load_inventory(day)
    cands = candidate_rows()
    cand_keys: set[str] = set()
    for row in cands:
        cand_keys.update(key_variants(row))
    rows_out: list[dict[str, Any]] = []
    reasons = Counter()
    b_cover = 0
    for row in inventory:
        b = book_count(row)
        c = context_count(row)
        if b < 1 or c < 1:
            if b < 1:
                reasons['not_b_cover_missing_bookmaker'] += 1
            if c < 1:
                reasons['not_b_cover_missing_context'] += 1
            continue
        b_cover += 1
        keys = key_variants(row)
        has_candidate = bool(keys & cand_keys)
        reason = 'has_candidate' if has_candidate else 'b_cover_no_candidate'
        if not has_candidate:
            if not has_xg(row):
                reason = 'b_cover_no_candidate_missing_xg_like_context'
            reasons[reason] += 1
        else:
            reasons['b_cover_has_candidate'] += 1
        if len(rows_out) < 300:
            rows_out.append({
                'match_key': row.get('match_key') or row.get('canonical_match_id'),
                'home_team': row.get('home_team') or row.get('home'),
                'away_team': row.get('away_team') or row.get('away'),
                'kickoff': row.get('commence_time') or row.get('kickoff_utc') or row.get('start_time'),
                'books_count': b,
                'context_count': c,
                'has_xg_like_context': has_xg(row),
                'has_candidate_in_latest_run': has_candidate,
                'gap_reason': reason,
            })
    report = {
        'created_at_utc': datetime.now(UTC).isoformat(),
        'target_date': day,
        'inventory_rows': len(inventory),
        'candidate_rows_seen': len(cands),
        'b_cover_rows': b_cover,
        'b_cover_without_candidate': sum(1 for r in rows_out if r['gap_reason'].startswith('b_cover_no_candidate')),
        'reason_counts': dict(reasons.most_common()),
        'sample': rows_out[:40],
    }
    write_json(REPORT_JSON, report)
    REPORT_CSV.parent.mkdir(parents=True, exist_ok=True)
    with REPORT_CSV.open('w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=['match_key', 'home_team', 'away_team', 'kickoff', 'books_count', 'context_count', 'has_xg_like_context', 'has_candidate_in_latest_run', 'gap_reason'])
        writer.writeheader()
        writer.writerows(rows_out)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
