from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path('.').resolve()
STATE_PATH = ROOT / '.data' / 'state.json'
OUT = ROOT / '.data' / 'exports' / 'latest-bankroll-state-cleanup.json'
UTC = timezone.utc


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


def is_valid_open(row: dict[str, Any], now: datetime, reset_at: datetime | None) -> bool:
    status = str(pick(row, 'status', 'settlement_status', 'result') or '').strip().lower()
    if status in {'settled', 'closed', 'won', 'lost', 'push', 'void', 'cancelled', 'canceled', 'refunded'}:
        return False
    sent_at = parse_dt(pick(row, 'sent_at', 'published_at', 'created_at', 'placed_at', 'timestamp'))
    if reset_at is not None and (sent_at is None or sent_at < reset_at):
        return False
    kickoff = parse_dt(pick(row, 'commence_time', 'start_time', 'kickoff'))
    if kickoff is not None and kickoff < now - timedelta(hours=6):
        return False
    stake = as_float(pick(row, 'stake_amount', 'stake', 'amount', 'risk', 'risk_amount'))
    return stake > 0.0


def normalize(row: dict[str, Any], current_balance: float) -> dict[str, Any]:
    stake = as_float(pick(row, 'stake_amount', 'stake', 'amount', 'risk', 'risk_amount'))
    stake_pct = as_float(pick(row, 'stake_pct', 'recommended_stake_pct'))
    if stake_pct <= 0 and current_balance > 0:
        stake_pct = stake / current_balance * 100.0
    out = dict(row)
    out.update({
        'status': 'pending',
        'settlement_status': 'open',
        'is_open': True,
        'open': True,
        'stake': round(stake, 2),
        'stake_amount': round(stake, 2),
        'amount': round(stake, 2),
        'risk': round(stake, 2),
        'risk_amount': round(stake, 2),
        'open_risk': round(stake, 2),
        'stake_pct': round(stake_pct, 2),
        'recommended_stake_pct': round(stake_pct, 2),
        'bankroll_snapshot': round(as_float(pick(row, 'bankroll_snapshot'), current_balance), 2),
        'dedupe_key': semantic_key(row),
    })
    return out


def main() -> int:
    now = datetime.now(UTC)
    state = load_json(STATE_PATH, {})
    if not isinstance(state, dict):
        state = {}
    bank = state.setdefault('bankroll', {})
    if not isinstance(bank, dict):
        bank = {}
        state['bankroll'] = bank
    reset_at = parse_dt(state.get('bankroll_reset_at_utc'))
    current_balance = as_float(bank.get('current_balance'), 1000.0) or 1000.0

    raw_rows: list[dict[str, Any]] = []
    for bucket in ('bets', 'published_candidates'):
        value = state.get(bucket)
        if isinstance(value, list):
            raw_rows.extend(row for row in value if isinstance(row, dict))

    unique: dict[str, dict[str, Any]] = {}
    removed = 0
    for row in raw_rows:
        if not is_valid_open(row, now, reset_at):
            removed += 1
            continue
        key = semantic_key(row)
        normalized = normalize(row, current_balance)
        if key not in unique or as_float(normalized.get('stake_amount')) > as_float(unique[key].get('stake_amount')):
            unique[key] = normalized

    open_bets = list(unique.values())
    open_exposure = round(sum(as_float(row.get('stake_amount')) for row in open_bets), 2)
    bank.update({
        'enabled': True,
        'currency': bank.get('currency') or 'units',
        'starting_balance': round(as_float(bank.get('starting_balance'), 1000.0) or 1000.0, 2),
        'current_balance': round(current_balance, 2),
        'peak_balance': round(max(as_float(bank.get('peak_balance')), current_balance, as_float(bank.get('starting_balance'), 1000.0)), 2),
        'open_exposure': open_exposure,
        'available_balance': round(max(0.0, current_balance - open_exposure), 2),
        'closed_pnl': round(as_float(bank.get('closed_pnl')), 2),
        'roi_pct': round((as_float(bank.get('closed_pnl')) / max(1.0, as_float(bank.get('starting_balance'), 1000.0))) * 100.0, 2),
    })
    state['bets'] = open_bets
    state['published_candidates'] = [dict(row) for row in open_bets]
    state['bankroll_state_cleaned_at_utc'] = now.isoformat()
    write_json(STATE_PATH, state)
    report = {
        'status': 'ok',
        'updated_at_utc': now.isoformat(),
        'reset_at_utc': reset_at.isoformat() if reset_at else None,
        'raw_rows_seen': len(raw_rows),
        'removed_rows': removed,
        'open_bets': len(open_bets),
        'open_exposure': open_exposure,
        'available_balance': bank['available_balance'],
    }
    write_json(OUT, report)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
