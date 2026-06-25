from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

UTC = timezone.utc
ROOT = Path('.').resolve()
EXPORT = ROOT / '.data' / 'exports'
OUT = EXPORT / 'latest-rescue-candidate-key-normalization.json'
TARGETS = [
    EXPORT / 'latest-rescue-candidates.json',
    ROOT / 'artifacts' / 'run-bot' / 'latest-rescue-candidates.json',
]


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


def norm(value: Any) -> str:
    text = str(value or '').strip().lower().replace('ё', 'е')
    text = text.replace('_', ' ')
    text = re.sub(r'[^a-z0-9а-я]+', ' ', text)
    return ' '.join(text.split())


def key_part(value: Any) -> str:
    return norm(value).replace(' ', '_')


def point_text(value: Any) -> str:
    try:
        if value in (None, ''):
            return ''
        number = float(str(value).replace(',', '.'))
        return str(int(number)) if number.is_integer() else f'{number:g}'
    except Exception:
        return norm(value)


def parse_dt(value: Any) -> str:
    if value in (None, ''):
        return ''
    text = str(value).strip()
    m = re.search(r'(20\d{2}-\d{2}-\d{2})', text)
    if m:
        return m.group(1)
    return ''


def rows_from_payload(payload: Any) -> tuple[list[dict[str, Any]], str | None]:
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)], None
    if isinstance(payload, dict):
        for key in ('candidates', 'rows', 'items', 'data', 'rescue_candidates'):
            value = payload.get(key)
            if isinstance(value, list):
                return [x for x in value if isinstance(x, dict)], key
    return [], None


def save_payload(path: Path, payload: Any, rows: list[dict[str, Any]], key: str | None) -> None:
    if isinstance(payload, list):
        write_json(path, rows)
    elif isinstance(payload, dict) and key:
        payload[key] = rows
        write_json(path, payload)


def canonical_match_key(row: dict[str, Any]) -> str:
    raw = str(row.get('canonical_match_id') or row.get('match_key') or row.get('event_key') or '').strip()
    date = parse_dt(row.get('commence_time') or row.get('kickoff') or row.get('start_time')) or parse_dt(raw)
    home = key_part(row.get('home_team') or row.get('home'))
    away = key_part(row.get('away_team') or row.get('away'))
    # Always prefer the explicit home/away fields when present. Some upstream
    # rescue rows carry a raw soccer|away|home|date key, which can bypass duplicate
    # guards. The displayed teams are the canonical publication identity.
    if date and home and away:
        return f'soccer|{home}|{away}|{date}'
    if raw:
        parts = [part.strip() for part in raw.split('|')]
        if len(parts) >= 4 and parts[0].lower() == 'soccer':
            return '|'.join(['soccer', key_part(parts[1]), key_part(parts[2]), parse_dt(parts[3]) or parts[3][:10]])
    return raw


def selection_key(row: dict[str, Any]) -> str:
    explicit = norm(row.get('selection_key'))
    if explicit in {'over', 'under'}:
        return explicit
    text = norm(row.get('selection') or row.get('outcome') or row.get('name'))
    if any(token in text for token in ('under', 'menshe', 'меньше', 'tm', 'тм')):
        return 'under'
    if any(token in text for token in ('over', 'bolshe', 'больше', 'tb', 'тб')):
        return 'over'
    return explicit or text


def normalize_row(row: dict[str, Any]) -> int:
    changed = 0
    canonical = canonical_match_key(row)
    for key in ('match_key', 'canonical_match_id'):
        if canonical and row.get(key) != canonical:
            row[f'source_{key}_before_normalization'] = row.get(key)
            row[key] = canonical
            changed += 1
    family = norm(row.get('family') or row.get('market_family'))
    if family in {'total', 'totals', 'тотал'}:
        if row.get('family') != 'totals':
            row['family'] = 'totals'
            changed += 1
        if row.get('market_family') != 'totals':
            row['market_family'] = 'totals'
            changed += 1
        family = 'totals'
    sel = selection_key(row)
    if sel and row.get('selection_key') != sel:
        row['selection_key'] = sel
        changed += 1
    point = point_text(row.get('point') or row.get('line') or row.get('handicap'))
    if canonical and family and sel and point:
        pub_key = f'{canonical}|{family}|{sel}|{point}|'
        if row.get('canonical_publication_key') != pub_key:
            row['canonical_publication_key'] = pub_key
            changed += 1
    return changed


def main() -> int:
    total_rows = 0
    total_changes = 0
    files = []
    for path in TARGETS:
        payload = load_json(path, None)
        rows, key = rows_from_payload(payload)
        if not rows:
            continue
        changes = 0
        for row in rows:
            changes += normalize_row(row)
        if changes:
            save_payload(path, payload, rows, key)
        total_rows += len(rows)
        total_changes += changes
        files.append({'path': str(path), 'rows': len(rows), 'changes': changes})
    report = {'status': 'ok', 'created_at_utc': datetime.now(UTC).isoformat(), 'rows_seen': total_rows, 'changes': total_changes, 'files': files}
    write_json(OUT, report)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
