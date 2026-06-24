from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

UTC = timezone.utc
ROOT = Path('.').resolve()
OUT = ROOT / '.data' / 'exports' / 'latest-persisted-publication-index-normalization.json'
INDEX_PATHS = [
    ROOT / '.data' / 'fallback-sent-index.json',
    ROOT / '.data' / 'published-candidate-index.json',
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
    text = str(value or '').strip().lower().replace('ё', 'е').replace('_', ' ')
    text = re.sub(r'[^a-z0-9а-я]+', ' ', text)
    return ' '.join(text.split())


def key_part(value: Any) -> str:
    return norm(value).replace(' ', '_')


def parse_date(value: Any) -> str:
    text = str(value or '')
    m = re.search(r'(20\d{2}-\d{2}-\d{2})', text)
    return m.group(1) if m else ''


def point_text(value: Any) -> str:
    try:
        if value in (None, ''):
            return ''
        number = float(str(value).replace(',', '.'))
        return str(int(number)) if number.is_integer() else f'{number:g}'
    except Exception:
        return norm(value)


def selection_key(row: dict[str, Any]) -> str:
    explicit = norm(row.get('selection_key'))
    if explicit in {'over', 'under', 'home', 'away', 'draw'}:
        return explicit
    text = norm(row.get('selection') or row.get('outcome') or row.get('name'))
    if any(token in text for token in ('under', 'меньше', 'tm', 'тм')):
        return 'under'
    if any(token in text for token in ('over', 'больше', 'tb', 'тб')):
        return 'over'
    return explicit or text


def family_key(row: dict[str, Any]) -> str:
    family = norm(row.get('family') or row.get('market_family'))
    if family in {'total', 'totals', 'тотал'}:
        return 'totals'
    if family in {'spread', 'spreads', 'handicap', 'фора'}:
        return 'spreads'
    return family


def canonical_match_key(row: dict[str, Any]) -> str:
    raw = str(row.get('canonical_match_id') or row.get('match_key') or row.get('event_key') or '')
    date = parse_date(row.get('commence_time') or row.get('kickoff') or row.get('start_time')) or parse_date(raw)
    home = key_part(row.get('home_team') or row.get('home'))
    away = key_part(row.get('away_team') or row.get('away'))
    if date and home and away:
        return f'soccer|{home}|{away}|{date}'
    parts = [part.strip() for part in raw.split('|')]
    if len(parts) >= 4 and parts[0].lower() == 'soccer':
        return f'soccer|{key_part(parts[1])}|{key_part(parts[2])}|{parse_date(parts[3]) or parts[3][:10]}'
    return raw


def canonical_publication_key(row: dict[str, Any]) -> str:
    match = canonical_match_key(row)
    family = family_key(row)
    selection = selection_key(row)
    point = point_text(row.get('point') or row.get('line') or row.get('handicap'))
    return f'{match}|{family}|{selection}|{point}|'


def row_timestamp(row: dict[str, Any]) -> str:
    return str(row.get('published_at') or row.get('sent_at') or row.get('created_at') or '')


def normalize_row(row: dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    match = canonical_match_key(out)
    pub = canonical_publication_key(out)
    if match:
        out['match_key'] = match
        out['canonical_match_id'] = match
    family = family_key(out)
    if family:
        out['family'] = family
        out['market_family'] = family
    selection = selection_key(out)
    if selection:
        out['selection_key'] = selection
    out['canonical_publication_key'] = pub
    out['publication_index_normalized_at_utc'] = datetime.now(UTC).isoformat()
    return out


def normalize_index(path: Path) -> dict[str, Any]:
    payload = load_json(path, {})
    if not isinstance(payload, dict):
        return {'path': str(path), 'status': 'skipped_not_dict'}
    rows = [row for row in payload.values() if isinstance(row, dict)]
    before = len(rows)
    by_key: dict[str, dict[str, Any]] = {}
    duplicate_rows = 0
    for row in rows:
        normalized = normalize_row(row)
        pub_key = str(normalized.get('canonical_publication_key') or '')
        if not pub_key.strip('|'):
            pub_key = json.dumps(normalized, ensure_ascii=False, sort_keys=True)[:500]
        if pub_key in by_key:
            duplicate_rows += 1
            # Keep the earliest sent/published row, but preserve the normalized key.
            existing_ts = row_timestamp(by_key[pub_key])
            new_ts = row_timestamp(normalized)
            if new_ts and (not existing_ts or new_ts < existing_ts):
                by_key[pub_key] = normalized
        else:
            by_key[pub_key] = normalized
    new_payload: dict[str, Any] = {}
    for pub_key, row in sorted(by_key.items(), key=lambda item: row_timestamp(item[1]) or item[0]):
        key = hashlib.sha1(pub_key.encode('utf-8')).hexdigest()
        new_payload[key] = row
    if new_payload != payload:
        write_json(path, new_payload)
    return {'path': str(path), 'status': 'ok', 'rows_before': before, 'rows_after': len(new_payload), 'duplicates_removed': duplicate_rows, 'changed': new_payload != payload}


def main() -> int:
    files = [normalize_index(path) for path in INDEX_PATHS]
    payload = {
        'status': 'ok',
        'created_at_utc': datetime.now(UTC).isoformat(),
        'files': files,
        'duplicates_removed': sum(int(item.get('duplicates_removed') or 0) for item in files),
    }
    write_json(OUT, payload)
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
