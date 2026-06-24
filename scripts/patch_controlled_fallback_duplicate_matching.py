from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

UTC = timezone.utc
ROOT = Path('.').resolve()
OUT = ROOT / '.data' / 'exports' / 'latest-controlled-fallback-duplicate-match-patch.json'


def write_json(payload: dict[str, Any]) -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')


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
        return str(value or '').strip().lower()


def install(v18: Any) -> dict[str, Any]:
    original = getattr(v18, '_same_candidate', None)
    signature = getattr(v18, '_candidate_signature', None)
    normalizer = getattr(v18, '_norm', lambda x: str(x or '').strip().lower())
    if not callable(original) or not callable(signature):
        payload = {'status': 'skipped', 'reason': 'required v18 functions missing'}
        write_json(payload)
        return payload

    def team_date(row: dict[str, Any]) -> tuple[str, str, str]:
        home = normalizer(row.get('home_team') or row.get('home'))
        away = normalizer(row.get('away_team') or row.get('away'))
        date = parse_date(row.get('commence_time') or row.get('kickoff') or row.get('start_time') or row.get('match_key') or row.get('canonical_match_id'))
        return home, away, date

    def same_candidate(candidate: dict[str, Any], row: dict[str, Any]) -> bool:
        if original(candidate, row):
            return True
        cand = signature(candidate)
        other = signature(row)
        if cand.get('family') and other.get('family') and cand.get('family') != other.get('family'):
            return False
        if cand.get('selection') and other.get('selection') and cand.get('selection') != other.get('selection'):
            return False
        c_point = cand.get('point') or point_text(candidate.get('point') or candidate.get('line') or candidate.get('handicap'))
        o_point = other.get('point') or point_text(row.get('point') or row.get('line') or row.get('handicap'))
        if c_point and o_point and c_point != o_point:
            return False
        ch, ca, cd = team_date(candidate)
        oh, oa, od = team_date(row)
        if not (ch and ca and oh and oa):
            return False
        same_order = ch == oh and ca == oa
        reversed_order = ch == oa and ca == oh
        same_day = not cd or not od or cd == od
        return same_day and (same_order or reversed_order)

    v18._same_candidate = same_candidate
    payload = {
        'status': 'installed',
        'created_at_utc': datetime.now(UTC).isoformat(),
        'rule': 'fallback duplicate matching compares teams/date/market/selection/point when stored match keys differ',
    }
    write_json(payload)
    return payload
