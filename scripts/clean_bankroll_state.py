from __future__ import annotations

import hashlib
import json
import os
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


def truthy(value: Any, default: bool = False) -> bool:
    raw = str(value if value is not None else '').strip().lower()
    if not raw:
        return default
    if raw in {'0', 'false', 'no', 'off', 'none', 'null'}:
        return False
    return raw in {'1', 'true', 'yes', 'on', 'force'}


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
    if status in {'settled', 'closed', 'won', 'lost', 'push', 'void', 'cancelled', 'canceled', 'refunded', 'half_won', 'half_lost'}:
        return False
    sent_at = parse_dt(pick(row, 'sent_at', 'published_at', 'published_at_utc', 'created_at', 'created_at_utc', 'placed_at', 'timestamp'))
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


def settlement_pnl(row: dict[str, Any]) -> float | None:
    status = str(pick(row, 'status', 'settlement_status', 'result', 'outcome') or '').strip().lower()
    settlement = row.get('settlement') if isinstance(row.get('settlement'), dict) else {}
    status = str(settlement.get('outcome') or settlement.get('result') or status).strip().lower()
    if status not in {'settled', 'closed', 'won', 'lost', 'push', 'void', 'cancelled', 'canceled', 'refunded', 'half_won', 'half_lost'}:
        return None
    direct = settlement.get('pnl') if isinstance(settlement, dict) else None
    if direct not in (None, ''):
        return as_float(direct)
    direct = row.get('pnl')
    if direct not in (None, ''):
        return as_float(direct)
    stake = as_float(pick(row, 'stake_amount', 'stake', 'amount', 'risk', 'risk_amount'))
    odds = as_float(pick(row, 'odds', 'selected_odds', 'price'))
    if status == 'won':
        return stake * max(0.0, odds - 1.0)
    if status == 'half_won':
        return stake * max(0.0, odds - 1.0) / 2.0
    if status == 'lost':
        return -stake
    if status == 'half_lost':
        return -stake / 2.0
    return 0.0


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

    starting_balance = round(as_float(bank.get('starting_balance'), 1000.0) or 1000.0, 2)
    previous_current = round(as_float(bank.get('current_balance'), starting_balance) or starting_balance, 2)
    previous_closed_pnl = round(as_float(bank.get('closed_pnl')), 2)

    raw_rows: list[dict[str, Any]] = []
    for bucket in ('bets', 'published_candidates'):
        value = state.get(bucket)
        if isinstance(value, list):
            raw_rows.extend(row for row in value if isinstance(row, dict))

    # Reconcile the bankroll from ledger/accounting, not from the stale current
    # balance.  Repeated report/settlement runs may re-import the same picks; if
    # current_balance is treated as authoritative it can drift upward while
    # closed_pnl stays correct.  Bank must always equal start + closed P&L.
    settled_pnls = [pnl for row in raw_rows for pnl in [settlement_pnl(row)] if pnl is not None]
    closed_pnl = round(sum(settled_pnls), 2) if settled_pnls else previous_closed_pnl
    current_balance = round(starting_balance + closed_pnl, 2) if truthy(os.getenv('BANKROLL_RECONCILE_FROM_CLOSED_PNL'), True) else previous_current

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
    available_balance = round(max(0.0, current_balance - open_exposure), 2)
    roi_pct = round((closed_pnl / max(1.0, starting_balance)) * 100.0, 2)
    bank.update({
        'enabled': True,
        'currency': bank.get('currency') or 'units',
        'starting_balance': starting_balance,
        'current_balance': current_balance,
        'peak_balance': round(max(starting_balance, current_balance), 2),
        'open_exposure': open_exposure,
        'available_balance': available_balance,
        'closed_pnl': closed_pnl,
        'roi_pct': roi_pct,
        'reconciled_from_closed_pnl': True,
        'previous_current_balance': previous_current,
        'previous_closed_pnl': previous_closed_pnl,
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
        'available_balance': available_balance,
        'starting_balance': starting_balance,
        'previous_current_balance': previous_current,
        'previous_closed_pnl': previous_closed_pnl,
        'closed_pnl': closed_pnl,
        'current_balance': current_balance,
        'roi_pct': roi_pct,
        'reconciled_from_closed_pnl': True,
    }
    write_json(OUT, report)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
