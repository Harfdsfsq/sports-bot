from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path('.').resolve()
DATA = ROOT / '.data'
STATE_PATH = DATA / 'state.json'
OUT = DATA / 'exports' / 'latest-bankroll-state-cleanup.json'
PAST_REPORT = DATA / 'exports' / 'latest-past-predictions-report.json'
PERFORMANCE_SUMMARY = DATA / 'bets' / 'performance-summary.json'

CLOSED = {'won', 'half_won', 'lost', 'half_lost', 'push', 'void', 'cancelled', 'canceled', 'refunded'}


def load_json(path: Path, default: Any) -> Any:
    try:
        if path.exists() and path.is_file() and path.stat().st_size > 0:
            return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        pass
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


def as_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ''):
            return default
        return float(str(value).replace(',', '.'))
    except Exception:
        return default


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


def nested(row: dict[str, Any], key: str) -> dict[str, Any]:
    value = row.get(key)
    return value if isinstance(value, dict) else {}


def first(*values: Any) -> Any:
    for value in values:
        if value not in (None, '', [], {}):
            return value
    return None


def norm(value: Any) -> str:
    text = str(value or '').strip().lower().replace('ё', 'е').replace('—', '-').replace('–', '-')
    text = re.sub(r'[^a-z0-9а-я]+', ' ', text)
    return ' '.join(text.split())


def point_text(value: Any) -> str:
    if value in (None, ''):
        return ''
    try:
        f = float(str(value).replace(',', '.'))
        return str(int(f)) if f.is_integer() else f'{f:.2f}'.rstrip('0').rstrip('.')
    except Exception:
        return norm(value)


def side(value: Any) -> str:
    text = norm(value)
    if any(x in text for x in ('under', 'меньше', 'тм', 'tm')):
        return 'under'
    if any(x in text for x in ('over', 'больше', 'тб', 'tb')):
        return 'over'
    return text


def pick(row: dict[str, Any], *names: str) -> Any:
    payload = nested(row, 'bet_payload')
    candidate = nested(row, 'candidate')
    for source in (row, payload, candidate):
        for name in names:
            value = source.get(name)
            if value not in (None, ''):
                return value
    return None


def row_status(row: dict[str, Any]) -> str:
    settlement = nested(row, 'settlement')
    raw = str(first(settlement.get('outcome'), settlement.get('result'), pick(row, 'status', 'settlement_status', 'result', 'outcome'), 'pending') or 'pending').strip().lower()
    if raw in {'settled', 'closed'}:
        outcome = str(first(settlement.get('outcome'), settlement.get('result'), row.get('outcome'), '') or '').strip().lower()
        return outcome if outcome in CLOSED else raw
    return raw if raw in CLOSED else 'pending'


def stake(row: dict[str, Any]) -> float:
    return max(as_float(pick(row, 'stake_amount', 'stake', 'amount', 'risk', 'risk_amount')), 0.0)


def odds(row: dict[str, Any]) -> float:
    return max(as_float(pick(row, 'odds', 'selected_odds', 'price')), 0.0)


def pnl(row: dict[str, Any]) -> float:
    settlement = nested(row, 'settlement')
    direct = first(settlement.get('pnl'), row.get('pnl'))
    if direct not in (None, ''):
        return as_float(direct)
    status = row_status(row)
    st = stake(row)
    od = odds(row)
    if status == 'won':
        return st * max(0.0, od - 1.0)
    if status == 'half_won':
        return st * max(0.0, od - 1.0) / 2.0
    if status == 'lost':
        return -st
    if status == 'half_lost':
        return -st / 2.0
    return 0.0


def business_key(row: dict[str, Any]) -> str:
    raw = '|'.join([
        norm(pick(row, 'home_team', 'home') or ''),
        norm(pick(row, 'away_team', 'away') or ''),
        norm(first(pick(row, 'family', 'market_family'), 'totals') or ''),
        side(first(pick(row, 'selection_key', 'selection'), '') or ''),
        point_text(pick(row, 'point', 'line', 'handicap')),
        f'{odds(row):.3f}' if odds(row) else '',
        row_status(row),
        f'{pnl(row):.2f}' if row_status(row) in CLOSED else 'pending',
    ])
    return hashlib.sha1(raw.encode('utf-8')).hexdigest()


def event_time(row: dict[str, Any]) -> datetime | None:
    for name in ('commence_time', 'kickoff_utc', 'kickoff', 'start_time', 'sent_at', 'published_at_utc', 'published_at', 'created_at_utc', 'created_at'):
        dt = parse_dt(pick(row, name))
        if dt is not None:
            return dt
    return None


def source_rows_from_report() -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    report = load_json(PAST_REPORT, {})
    if isinstance(report, dict) and isinstance(report.get('rows'), list):
        return [r for r in report.get('rows') if isinstance(r, dict)], 'latest-past-predictions-report', report.get('summary') if isinstance(report.get('summary'), dict) else {}
    perf = load_json(PERFORMANCE_SUMMARY, {})
    if isinstance(perf, dict) and isinstance(perf.get('rows'), list):
        return [r for r in perf.get('rows') if isinstance(r, dict)], 'performance-summary-rows', perf.get('summary') if isinstance(perf.get('summary'), dict) else {}
    return [], 'state-fallback', {}


def state_rows(state: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for bucket in ('bets', 'published_candidates'):
        value = state.get(bucket)
        if isinstance(value, list):
            rows.extend(row for row in value if isinstance(row, dict))
    return rows


def unique_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_key: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = str(row.get('business_dedupe_key') or row.get('ledger_semantic_key') or business_key(row))
        current = by_key.get(key)
        if current is None:
            by_key[key] = dict(row)
            continue
        cur_score = (1 if row_status(current) in CLOSED else 0, len(json.dumps(current, ensure_ascii=False)))
        new_score = (1 if row_status(row) in CLOSED else 0, len(json.dumps(row, ensure_ascii=False)))
        if new_score >= cur_score:
            by_key[key] = dict(row)
    return list(by_key.values())


def is_live_open(row: dict[str, Any], now: datetime) -> bool:
    if row_status(row) != 'pending':
        return False
    dt = parse_dt(pick(row, 'commence_time', 'kickoff_utc', 'kickoff', 'start_time'))
    if dt is None:
        dt = event_time(row)
    # A bet is real open exposure only while the match has not clearly aged out.
    # Old pending rows stay in the passability report as needs_result_settlement,
    # but they should not keep reducing available bankroll for days.
    stale_hours = int(float(os.getenv('BANKROLL_PENDING_STALE_HOURS', '6') or 6))
    if dt is not None and dt < now - timedelta(hours=stale_hours):
        return False
    return stake(row) > 0


def normalize_open(row: dict[str, Any], current_balance: float) -> dict[str, Any]:
    st = stake(row) or 5.0
    stake_pct = as_float(pick(row, 'stake_pct', 'recommended_stake_pct'))
    if stake_pct <= 0 and current_balance > 0:
        stake_pct = st / current_balance * 100.0
    out = dict(row)
    out.update({
        'status': 'pending',
        'settlement_status': 'open',
        'is_open': True,
        'open': True,
        'stake': round(st, 2),
        'stake_amount': round(st, 2),
        'amount': round(st, 2),
        'risk': round(st, 2),
        'risk_amount': round(st, 2),
        'open_risk': round(st, 2),
        'stake_pct': round(stake_pct, 2),
        'recommended_stake_pct': round(stake_pct, 2),
        'bankroll_snapshot': round(current_balance, 2),
        'dedupe_key': str(row.get('business_dedupe_key') or row.get('ledger_semantic_key') or business_key(row)),
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

    starting_balance = round(as_float(bank.get('starting_balance'), 1000.0) or 1000.0, 2)
    previous_current = round(as_float(bank.get('current_balance'), starting_balance) or starting_balance, 2)
    previous_closed_pnl = round(as_float(bank.get('closed_pnl')), 2)

    report_rows, source, report_summary = source_rows_from_report()
    rows = unique_rows(report_rows) if report_rows else unique_rows(state_rows(state))
    perf = load_json(PERFORMANCE_SUMMARY, {})
    perf_summary = perf.get('summary') if isinstance(perf, dict) and isinstance(perf.get('summary'), dict) else {}

    closed_rows = [r for r in rows if row_status(r) in CLOSED]
    pending_rows = [r for r in rows if row_status(r) == 'pending']
    live_pending_rows = [r for r in pending_rows if is_live_open(r, now)]
    stale_pending_rows = [r for r in pending_rows if r not in live_pending_rows]

    summary_pnl = first(report_summary.get('pnl') if isinstance(report_summary, dict) else None, perf_summary.get('pnl') if isinstance(perf_summary, dict) else None)
    if summary_pnl not in (None, '') and truthy(os.getenv('BANKROLL_USE_PERFORMANCE_SUMMARY_PNL'), True):
        closed_pnl = round(as_float(summary_pnl), 2)
    elif closed_rows:
        closed_pnl = round(sum(pnl(r) for r in closed_rows), 2)
    else:
        closed_pnl = previous_closed_pnl

    current_balance = round(starting_balance + closed_pnl, 2) if truthy(os.getenv('BANKROLL_RECONCILE_FROM_CLOSED_PNL'), True) else previous_current
    open_bets = [normalize_open(r, current_balance) for r in live_pending_rows]
    stale_unsettled_stake = round(sum(stake(r) or 5.0 for r in stale_pending_rows), 2)
    open_exposure = round(sum(stake(r) for r in open_bets), 2)
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
        'reconciled_from_report_source': source,
        'stale_unsettled_count': len(stale_pending_rows),
        'stale_unsettled_stake': stale_unsettled_stake,
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
        'source': source,
        'raw_report_rows': len(report_rows),
        'unique_rows': len(rows),
        'closed_rows': len(closed_rows),
        'pending_rows': len(pending_rows),
        'live_pending_rows': len(live_pending_rows),
        'stale_pending_rows': len(stale_pending_rows),
        'stale_unsettled_stake': stale_unsettled_stake,
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
        'performance_summary_pnl_used': summary_pnl not in (None, '') and truthy(os.getenv('BANKROLL_USE_PERFORMANCE_SUMMARY_PNL'), True),
    }
    write_json(OUT, report)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
