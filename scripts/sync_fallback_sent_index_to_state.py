from __future__ import annotations

"""Materialize controlled fallback publications into state.json safely.

Rules:
- never import rows published before the latest bankroll reset;
- never duplicate the same match/market/selection as a new open bet;
- never keep obviously finished/old matches as open exposure;
- compute open exposure from unique pending bets only.
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
        if not path.exists() or not path.is_file():
            return default
        return json.loads(path.read_text(encoding='utf-8'))
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


def first_float(row: dict[str, Any], names: list[str], default: float = 0.0) -> float:
    for name in names:
        value = pick(row, name)
        if value not in (None, ''):
            parsed = as_float(value, default)
            if parsed != default or str(value).strip() in {'0', '0.0'}:
                return parsed
    return default


def semantic_key(row: dict[str, Any]) -> str:
    raw = '|'.join([
        str(pick(row, 'match_key') or '').strip().lower(),
        str(pick(row, 'home_team', 'home') or '').strip().lower(),
        str(pick(row, 'away_team', 'away') or '').strip().lower(),
        str(pick(row, 'commence_time', 'start_time', 'kickoff') or '')[:16],
        str(pick(row, 'family') or '').strip().lower(),
        str(pick(row, 'selection') or '').strip().lower(),
        str(pick(row, 'selection_key') or '').strip().lower(),
        str(pick(row, 'point') or '').strip(),
        str(pick(row, 'team_side') or '').strip().lower(),
    ])
    return hashlib.sha1(raw.encode('utf-8')).hexdigest()


def is_after_reset(row: dict[str, Any], reset_at: datetime | None) -> bool:
    if reset_at is None:
        return True
    sent_at = parse_dt(pick(row, 'sent_at', 'published_at', 'created_at', 'placed_at', 'timestamp'))
    if sent_at is None:
        return False
    return sent_at >= reset_at


def is_still_open(row: dict[str, Any], now: datetime) -> bool:
    status = str(pick(row, 'status', 'settlement_status', 'result') or '').strip().lower()
    if status in {'settled', 'closed', 'won', 'lost', 'push', 'void', 'cancelled', 'canceled', 'refunded'}:
        return False
    kickoff = parse_dt(pick(row, 'commence_time', 'start_time', 'kickoff'))
    if kickoff is not None and kickoff < now - timedelta(hours=6):
        return False
    return True


def normalize_open_bet(row: dict[str, Any], source_key: str) -> dict[str, Any]:
    stake = first_float(row, ['stake_amount', 'stake', 'amount', 'bet_amount', 'risk', 'risk_amount', 'recommended_stake'], 0.0)
    odds = first_float(row, ['odds', 'price', 'decimal_odds'], 0.0)
    sent_at = pick(row, 'sent_at', 'published_at', 'created_at', 'placed_at', 'timestamp') or datetime.now(UTC).isoformat()
    bankroll_snapshot = first_float(row, ['bankroll_snapshot'], 1000.0)
    stake_pct = first_float(row, ['stake_pct', 'recommended_stake_pct'], 0.0)
    if stake_pct <= 0 and stake > 0 and bankroll_snapshot > 0:
        stake_pct = stake / bankroll_snapshot * 100.0
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
        'stake': round(stake, 2),
        'stake_amount': round(stake, 2),
        'amount': round(stake, 2),
        'risk': round(stake, 2),
        'risk_amount': round(stake, 2),
        'open_risk': round(stake, 2),
        'stake_pct': round(stake_pct, 2),
        'recommended_stake_pct': round(stake_pct, 2),
        'bankroll_snapshot': round(bankroll_snapshot, 2),
        'odds': odds,
        'match_key': pick(row, 'match_key') or '',
        'family': pick(row, 'family') or '',
        'selection': pick(row, 'selection') or '',
        'selection_key': pick(row, 'selection_key') or '',
        'point': pick(row, 'point') or '',
        'team_side': pick(row, 'team_side') or '',
        'home_team': pick(row, 'home_team', 'home') or '',
        'away_team': pick(row, 'away_team', 'away') or '',
        'commence_time': pick(row, 'commence_time', 'start_time', 'kickoff') or '',
        'dedupe_key': semantic_key(row),
        'fallback_sent_index_key': source_key,
        '_synced_from_fallback_sent_index': True,
    })
    return normalized


def unique_open_bets(rows: list[Any], now: datetime, reset_at: datetime | None) -> list[dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for item in rows:
        if not isinstance(item, dict):
            continue
        if not is_after_reset(item, reset_at):
            continue
        if not is_still_open(item, now):
            continue
        key = semantic_key(item)
        if not key:
            continue
        stake = first_float(item, ['stake_amount', 'stake', 'amount', 'risk', 'risk_amount'], 0.0)
        # Prefer the row with a real stake amount over old zero-stake legacy rows.
        if key not in out or stake > first_float(out[key], ['stake_amount', 'stake', 'amount', 'risk', 'risk_amount'], 0.0):
            out[key] = dict(item)
    return list(out.values())


def main() -> int:
    now = datetime.now(UTC)
    sent_index = load_json(SENT_INDEX_PATH, {})
    if not isinstance(sent_index, dict):
        sent_index = {}
    state = load_json(STATE_PATH, {})
    if not isinstance(state, dict):
        state = {}
    bank = state.setdefault('bankroll', {})
    if not isinstance(bank, dict):
        bank = {}
        state['bankroll'] = bank
    reset_at = parse_dt(state.get('bankroll_reset_at_utc'))

    current_rows = []
    for bucket in ('bets', 'published_candidates'):
        raw = state.get(bucket)
        if isinstance(raw, list):
            current_rows.extend(x for x in raw if isinstance(x, dict))
    unique: dict[str, dict[str, Any]] = {semantic_key(row): row for row in unique_open_bets(current_rows, now, reset_at)}

    considered = 0
    added = 0
    skipped_before_reset = 0
    skipped_old_closed = 0
    skipped_duplicate = 0
    for key, row in sent_index.items():
        if not isinstance(row, dict):
            continue
        considered += 1
        if not is_after_reset(row, reset_at):
            skipped_before_reset += 1
            continue
        if not is_still_open(row, now):
            skipped_old_closed += 1
            continue
        normalized = normalize_open_bet(row, str(key))
        skey = semantic_key(normalized)
        if skey in unique:
            skipped_duplicate += 1
            if as_float(normalized.get('stake_amount')) > as_float(unique[skey].get('stake_amount')):
                unique[skey] = normalized
            continue
        unique[skey] = normalized
        added += 1

    open_bets = list(unique.values())
    open_exposure = round(sum(max(0.0, as_float(row.get('stake_amount') or row.get('stake') or row.get('risk'))) for row in open_bets), 2)
    current_balance = as_float(bank.get('current_balance'), 1000.0) or 1000.0
    bank['current_balance'] = round(current_balance, 2)
    bank['starting_balance'] = round(as_float(bank.get('starting_balance'), 1000.0) or 1000.0, 2)
    bank['open_exposure'] = open_exposure
    bank['available_balance'] = round(max(0.0, current_balance - open_exposure), 2)
    bank['roi_pct'] = round(as_float(bank.get('closed_pnl')) / bank['starting_balance'] * 100.0 if bank['starting_balance'] else 0.0, 2)

    state['bets'] = open_bets
    state['published_candidates'] = [dict(row) for row in open_bets]
    state['fallback_sent_index_synced_at'] = now.isoformat()
    write_json(STATE_PATH, state)

    report = {
        'status': 'ok',
        'updated_at_utc': now.isoformat(),
        'reset_at_utc': reset_at.isoformat() if reset_at else None,
        'sent_index_rows': len(sent_index),
        'considered': considered,
        'added': added,
        'skipped_before_reset': skipped_before_reset,
        'skipped_old_closed': skipped_old_closed,
        'skipped_duplicate': skipped_duplicate,
        'open_bets_total': len(open_bets),
        'open_exposure': open_exposure,
        'available_balance': bank['available_balance'],
    }
    write_json(OUT, report)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
