from __future__ import annotations

"""Merge fallback Telegram publications into .data/state.json as open bets.

The duplicate guard already uses .data/fallback-sent-index.json, but the bankroll
header can still show open risk as 0.00 if state.json does not contain those
controlled fallback picks. This script materializes recent fallback publications
into state['bets'] and state['published_candidates'] with a conservative pending
status and multiple stake field aliases so existing bankroll/reporting code can
read the exposure without knowing the fallback schema.
"""

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

UTC = timezone.utc
ROOT = Path('.').resolve()
SENT_INDEX_PATH = ROOT / '.data' / 'fallback-sent-index.json'
STATE_PATH = ROOT / '.data' / 'state.json'
OUT = ROOT / '.data' / 'exports' / 'latest-fallback-state-sync.json'


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding='utf-8')) if path.exists() else default
    except Exception:
        return default


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')


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


def as_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ''):
            return default
        return float(value)
    except Exception:
        return default


def first_float(row: dict[str, Any], names: list[str], default: float = 0.0) -> float:
    for name in names:
        value = row.get(name)
        if value not in (None, ''):
            parsed = as_float(value, default)
            if parsed != default or str(value).strip() in {'0', '0.0'}:
                return parsed
    # Some sent-index rows keep the candidate payload nested.
    candidate = row.get('candidate') if isinstance(row.get('candidate'), dict) else {}
    for name in names:
        value = candidate.get(name)
        if value not in (None, ''):
            return as_float(value, default)
    return default


def pick(row: dict[str, Any], *names: str) -> Any:
    for name in names:
        value = row.get(name)
        if value not in (None, ''):
            return value
    candidate = row.get('candidate') if isinstance(row.get('candidate'), dict) else {}
    for name in names:
        value = candidate.get(name)
        if value not in (None, ''):
            return value
    return None


def dedupe_key(row: dict[str, Any]) -> str:
    explicit = str(pick(row, 'dedupe_key', 'fallback_dedupe_key', 'bet_id', 'id') or '').strip()
    if explicit:
        return explicit
    raw = '|'.join([
        str(pick(row, 'match_key') or ''),
        str(pick(row, 'family') or '').lower(),
        str(pick(row, 'selection') or '').lower(),
        str(pick(row, 'selection_key') or '').lower(),
        str(pick(row, 'point') or ''),
        str(pick(row, 'team_side') or '').lower(),
        str(pick(row, 'commence_time', 'start_time', 'kickoff') or ''),
    ])
    return hashlib.sha1(raw.encode('utf-8')).hexdigest()


def match_key(row: dict[str, Any]) -> str:
    explicit = str(pick(row, 'match_key') or '').strip().lower()
    if explicit:
        return explicit
    raw = '|'.join([
        str(pick(row, 'home_team', 'home') or '').strip().lower(),
        str(pick(row, 'away_team', 'away') or '').strip().lower(),
        str(pick(row, 'commence_time', 'start_time', 'kickoff') or '')[:16],
    ])
    return hashlib.sha1(raw.encode('utf-8')).hexdigest() if raw.strip('|') else ''


def is_recent_open(row: dict[str, Any], now: datetime, max_hours: int = 72) -> bool:
    status = str(pick(row, 'status', 'settlement_status', 'result') or '').strip().lower()
    if status in {'settled', 'closed', 'won', 'lost', 'push', 'void', 'cancelled', 'canceled', 'refunded'}:
        return False
    kickoff = parse_dt(pick(row, 'commence_time', 'start_time', 'kickoff'))
    sent_at = parse_dt(pick(row, 'sent_at', 'published_at', 'created_at', 'placed_at', 'timestamp'))
    # Keep open until a settlement job marks it closed; still prune very old rows to avoid stale exposure.
    anchor = kickoff or sent_at
    if anchor is not None and anchor < now - timedelta(hours=max(1, max_hours)):
        return False
    return True


def normalize_open_bet(row: dict[str, Any], source_key: str) -> dict[str, Any]:
    stake = first_float(row, ['stake', 'stake_amount', 'amount', 'bet_amount', 'risk', 'risk_amount', 'recommended_stake'], 0.0)
    odds = first_float(row, ['odds', 'price', 'decimal_odds'], 0.0)
    sent_at = pick(row, 'sent_at', 'published_at', 'created_at', 'placed_at', 'timestamp') or datetime.now(UTC).isoformat()
    normalized = dict(row)
    normalized.update({
        'id': pick(row, 'id', 'bet_id') or f'fallback:{source_key}',
        'bet_id': pick(row, 'bet_id', 'id') or f'fallback:{source_key}',
        'source': 'controlled_fallback',
        'publication_source': 'controlled_fallback',
        'status': 'pending',
        'settlement_status': 'open',
        'is_open': True,
        'open': True,
        'sent_at': sent_at,
        'published_at': pick(row, 'published_at') or sent_at,
        'created_at': pick(row, 'created_at') or sent_at,
        'stake': stake,
        'stake_amount': stake,
        'amount': stake,
        'risk': stake,
        'risk_amount': stake,
        'open_risk': stake,
        'odds': odds,
        'match_key': pick(row, 'match_key') or match_key(row),
        'family': pick(row, 'family') or '',
        'selection': pick(row, 'selection') or '',
        'point': pick(row, 'point') or '',
        'home_team': pick(row, 'home_team', 'home') or '',
        'away_team': pick(row, 'away_team', 'away') or '',
        'commence_time': pick(row, 'commence_time', 'start_time', 'kickoff') or '',
        'dedupe_key': dedupe_key(row),
        'fallback_sent_index_key': source_key,
        '_synced_from_fallback_sent_index': True,
    })
    return normalized


def existing_keys(rows: list[Any]) -> set[str]:
    keys: set[str] = set()
    for row in rows:
        if isinstance(row, dict):
            keys.add(dedupe_key(row))
            mid = match_key(row)
            if mid:
                keys.add('match:' + mid)
    return keys


def main() -> int:
    now = datetime.now(UTC)
    sent_index = load_json(SENT_INDEX_PATH, {})
    if not isinstance(sent_index, dict):
        sent_index = {}
    state = load_json(STATE_PATH, {})
    if not isinstance(state, dict):
        state = {}
    state.setdefault('bets', [])
    state.setdefault('published_candidates', [])
    if not isinstance(state['bets'], list):
        state['bets'] = []
    if not isinstance(state['published_candidates'], list):
        state['published_candidates'] = []

    keys = existing_keys(state['bets']) | existing_keys(state['published_candidates'])
    added_bets = 0
    added_published = 0
    skipped = 0
    open_risk_added = 0.0
    considered = 0

    for key, row in sent_index.items():
        if not isinstance(row, dict):
            skipped += 1
            continue
        considered += 1
        if not is_recent_open(row, now, max_hours=72):
            skipped += 1
            continue
        normalized = normalize_open_bet(row, str(key))
        dkey = dedupe_key(normalized)
        mkey = match_key(normalized)
        if dkey in keys or (mkey and 'match:' + mkey in keys):
            skipped += 1
            continue
        state['bets'].append(normalized)
        state['published_candidates'].append(dict(normalized))
        keys.add(dkey)
        if mkey:
            keys.add('match:' + mkey)
        added_bets += 1
        added_published += 1
        open_risk_added += as_float(normalized.get('stake'), 0.0)

    state['fallback_sent_index_synced_at'] = now.isoformat()
    write_json(STATE_PATH, state)

    report = {
        'status': 'ok',
        'updated_at_utc': now.isoformat(),
        'sent_index_rows': len(sent_index),
        'considered_recent_open': considered,
        'added_bets': added_bets,
        'added_published_candidates': added_published,
        'skipped': skipped,
        'open_risk_added': round(open_risk_added, 2),
        'state_bets_total': len(state.get('bets') or []),
        'state_published_candidates_total': len(state.get('published_candidates') or []),
    }
    write_json(OUT, report)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
