from __future__ import annotations

import json
import math
import os
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path('.').resolve()
UTC = timezone.utc
STATE_PATH = ROOT / '.data' / 'state.json'
OUT = ROOT / '.data' / 'exports' / 'latest-bankroll-control-audit.json'
DEFAULT_BANKROLL = 1000.0
CANDIDATE_PATHS = [
    ROOT / '.data' / 'exports' / 'latest-rescue-candidates.json',
    ROOT / 'artifacts' / 'run-bot' / 'latest-rescue-candidates.json',
    ROOT / '.logs' / 'debug-last-run.json',
    ROOT / 'artifacts' / 'controlled-fallback-report.json',
    ROOT / '.data' / 'exports' / 'latest-controlled-fallback-report.json',
]
DAILY_REPORT_PATHS = [
    ROOT / '.data' / 'exports' / 'latest-daily-report.json',
    ROOT / '.data' / 'exports' / 'latest-daily-summary.json',
]


def env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == '':
        return default
    return str(raw).strip().lower() in {'1', 'true', 'yes', 'on'}


def env_float(name: str, default: float) -> float:
    try:
        raw = os.getenv(name)
        return float(raw) if raw not in (None, '') else default
    except Exception:
        return default


def load_json(path: Path, default: Any) -> Any:
    try:
        if not path.exists() or not path.is_file():
            return default
        text = path.read_text(encoding='utf-8')
        if not text.strip():
            return default
        return json.loads(text)
    except Exception:
        return default


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')


def as_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ''):
            return default
        value = float(value)
        if math.isnan(value) or math.isinf(value):
            return default
        return value
    except Exception:
        return default


def default_state() -> dict[str, Any]:
    now = datetime.now(UTC).isoformat()
    return {
        'version': 4,
        'updated_at': now,
        'last_run': {},
        'bankroll': {
            'enabled': True,
            'currency': os.getenv('BANKROLL_CURRENCY') or 'units',
            'starting_balance': DEFAULT_BANKROLL,
            'current_balance': DEFAULT_BANKROLL,
            'peak_balance': DEFAULT_BANKROLL,
            'open_exposure': 0.0,
            'closed_pnl': 0.0,
            'total_staked': 0.0,
            'bets_published': 0,
            'bets_settled': 0,
            'wins': 0,
            'losses': 0,
            'pushes': 0,
            'voids': 0,
        },
        'bets': [],
        'shadow_bets': [],
        'published_candidates': [],
        'daily_reports': {},
        'run_history': [],
        'message_history': [],
        'learning_state': {},
    }


def load_state() -> dict[str, Any]:
    state = load_json(STATE_PATH, {})
    base = default_state()
    if isinstance(state, dict) and state:
        base.update({k: v for k, v in state.items() if k in base})
        if isinstance(state.get('bankroll'), dict):
            base['bankroll'].update(state['bankroll'])
    return base


def pending_exposure(state: dict[str, Any]) -> float:
    total = 0.0
    for item in state.get('bets') or []:
        if isinstance(item, dict) and str(item.get('status') or '') == 'pending':
            total += as_float(item.get('stake_amount'))
    return round(total, 2)


def normalize_bankroll(state: dict[str, Any], *, reset: bool) -> dict[str, Any]:
    bank = state.setdefault('bankroll', {})
    if not isinstance(bank, dict):
        bank = {}
        state['bankroll'] = bank
    current_before = as_float(bank.get('current_balance'), DEFAULT_BANKROLL)
    open_before = as_float(bank.get('open_exposure'), 0.0)
    if reset:
        bank.update({
            'enabled': True,
            'currency': os.getenv('BANKROLL_CURRENCY') or bank.get('currency') or 'units',
            'starting_balance': DEFAULT_BANKROLL,
            'current_balance': DEFAULT_BANKROLL,
            'peak_balance': DEFAULT_BANKROLL,
            'open_exposure': 0.0,
            'closed_pnl': 0.0,
            'total_staked': 0.0,
            'bets_published': 0,
            'bets_settled': 0,
            'wins': 0,
            'losses': 0,
            'pushes': 0,
            'voids': 0,
        })
        state['bets'] = []
        state['published_candidates'] = []
        state['shadow_bets'] = []
        state['daily_reports'] = {}
        state.setdefault('learning_state', {})
        state['bankroll_reset_at_utc'] = datetime.now(UTC).isoformat()
        state['bankroll_reset_reason'] = 'manual_reset_to_1000'
    else:
        start = as_float(bank.get('starting_balance'), DEFAULT_BANKROLL)
        current = as_float(bank.get('current_balance'), start or DEFAULT_BANKROLL)
        open_exp = pending_exposure(state)
        bank['enabled'] = bool(bank.get('enabled', True))
        bank['currency'] = os.getenv('BANKROLL_CURRENCY') or bank.get('currency') or 'units'
        bank['starting_balance'] = round(start if start > 0 else DEFAULT_BANKROLL, 2)
        bank['current_balance'] = round(current if current > 0 else bank['starting_balance'], 2)
        bank['peak_balance'] = round(max(as_float(bank.get('peak_balance')), bank['current_balance'], bank['starting_balance']), 2)
        bank['open_exposure'] = round(open_exp, 2)
        bank['available_balance'] = round(max(0.0, bank['current_balance'] - bank['open_exposure']), 2)
        bank['closed_pnl'] = round(as_float(bank.get('closed_pnl')), 2)
        bank['total_staked'] = round(as_float(bank.get('total_staked')), 2)
    bank['available_balance'] = round(max(0.0, as_float(bank.get('current_balance')) - as_float(bank.get('open_exposure'))), 2)
    start = as_float(bank.get('starting_balance'))
    closed = as_float(bank.get('closed_pnl'))
    total_staked = as_float(bank.get('total_staked'))
    bank['roi_pct'] = round((closed / start * 100.0) if start > 0 else 0.0, 2)
    bank['yield_pct'] = round((closed / total_staked * 100.0) if total_staked > 0 else 0.0, 2)
    return {'current_before': current_before, 'open_exposure_before': open_before, 'bankroll': dict(bank)}


def row_is_candidate(row: dict[str, Any]) -> bool:
    if not isinstance(row, dict):
        return False
    if row.get('_filtered_out'):
        return False
    family = str(row.get('family') or row.get('market_family') or '').strip().lower()
    if family not in {'totals', 'dnb', 'btts', 'h2h', 'spreads', 'teamtotals', 'doublechance'}:
        return False
    odds = as_float(row.get('odds') or row.get('price') or row.get('selected_odds'))
    return odds > 1.0


def probability(row: dict[str, Any]) -> float:
    for key in ('adjusted_probability', 'canonical_adjusted_probability', 'final_probability', 'model_probability'):
        value = as_float(row.get(key), 0.0)
        if value > 1.0:
            value /= 100.0
        if 0.0 < value < 1.0:
            return value
    odds = as_float(row.get('odds') or row.get('price') or row.get('selected_odds'))
    return 1.0 / odds if odds > 1.0 else 0.0


def stake_pct_for(row: dict[str, Any]) -> float:
    existing = as_float(row.get('stake_pct'), 0.0)
    if existing > 0:
        return existing
    min_pct = env_float('BANKROLL_MIN_STAKE_PCT', 1.0)
    max_pct = env_float('BANKROLL_MAX_STAKE_PCT', 6.0)
    flat_pct = env_float('BANKROLL_FLAT_STAKE_PCT', 3.0)
    if not env_bool('BANKROLL_KELLY_ENABLED', True):
        return max(min_pct, min(max_pct, flat_pct))
    odds = as_float(row.get('odds') or row.get('price') or row.get('selected_odds'))
    p = probability(row)
    b = odds - 1.0
    if p <= 0.0 or b <= 0.0:
        return max(min_pct, min(max_pct, flat_pct))
    kelly = ((b * p) - (1.0 - p)) / b
    if kelly <= 0:
        return max(min_pct, min(max_pct, flat_pct * 0.75))
    pct = kelly * 100.0 * env_float('BANKROLL_KELLY_FRACTION', 0.35)
    return round(max(min_pct, min(max_pct, pct)), 2)


def enrich_row(row: dict[str, Any], bank: dict[str, Any]) -> bool:
    if not row_is_candidate(row):
        return False
    current = as_float(bank.get('current_balance'), DEFAULT_BANKROLL)
    if current <= 0:
        current = DEFAULT_BANKROLL
    existing_stake = as_float(row.get('stake_amount'), 0.0)
    pct = stake_pct_for(row)
    if existing_stake > 0:
        pct = round(existing_stake / current * 100.0, 2)
    stake = existing_stake if existing_stake > 0 else current * pct / 100.0
    min_amount = env_float('BANKROLL_MIN_STAKE_AMOUNT', 10.0)
    round_to = env_float('BANKROLL_ROUND_TO', 1.0)
    if existing_stake <= 0 and stake > 0:
        stake = max(min_amount, stake)
        if round_to > 0:
            stake = round(round(stake / round_to) * round_to, 2)
    row['bankroll_snapshot'] = round(current, 2)
    row['stake_amount'] = round(stake, 2)
    row['stake_pct'] = round((stake / current * 100.0) if current > 0 and stake > 0 else pct, 2)
    row['recommended_stake_pct'] = row['stake_pct']
    row['recommended_stake_text'] = f"{row['stake_pct']:.2f}% банка / {row['stake_amount']:.2f} {bank.get('currency') or 'units'}"
    row['bankroll_currency'] = str(bank.get('currency') or 'units')
    row['risk_label'] = 'high' if row['stake_pct'] >= 5.0 else 'medium' if row['stake_pct'] >= 3.0 else 'low'
    ss = row.get('source_summary') if isinstance(row.get('source_summary'), dict) else {}
    ss = dict(ss)
    ss['recommended_stake_pct'] = row['stake_pct']
    ss['recommended_stake_amount'] = row['stake_amount']
    ss['bankroll_snapshot'] = row['bankroll_snapshot']
    row['source_summary'] = ss
    return True


def enrich_payload(payload: Any, bank: dict[str, Any]) -> tuple[Any, int]:
    changed = 0

    def walk(value: Any) -> Any:
        nonlocal changed
        if isinstance(value, list):
            return [walk(item) for item in value]
        if isinstance(value, dict):
            out = {key: walk(item) for key, item in value.items()}
            if enrich_row(out, bank):
                changed += 1
            return out
        return value

    return walk(deepcopy(payload)), changed


def check_daily_reports() -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    for path in DAILY_REPORT_PATHS:
        payload = load_json(path, None)
        if payload is None:
            checks.append({'path': str(path), 'status': 'missing'})
            continue
        status = 'ok'
        problems: list[str] = []
        if isinstance(payload, dict):
            summary = payload.get('summary') if isinstance(payload.get('summary'), dict) else payload
            total = as_float(summary.get('total_bets'), 0.0) if isinstance(summary, dict) else 0.0
            corrupted = as_float(summary.get('corrupted_bets'), 0.0) if isinstance(summary, dict) else 0.0
            if corrupted > 0:
                problems.append('corrupted_bets_present')
            if total < 0:
                problems.append('negative_total_bets')
        if problems:
            status = 'warning'
        checks.append({'path': str(path), 'status': status, 'problems': problems})
    return {'checks': checks, 'warnings': sum(1 for item in checks if item['status'] == 'warning')}


def main() -> int:
    reset = env_bool('BANKROLL_RESET_NOW', False) or env_bool('BANKROLL_FORCE_RESET_TO_1000', False)
    state = load_state()
    bankroll_change = normalize_bankroll(state, reset=reset)
    state['updated_at'] = datetime.now(UTC).isoformat()
    write_json(STATE_PATH, state)
    bank = dict(state.get('bankroll') or {})

    file_reports: list[dict[str, Any]] = []
    total_enriched = 0
    for path in CANDIDATE_PATHS:
        payload = load_json(path, None)
        if payload is None:
            file_reports.append({'path': str(path), 'status': 'missing_or_invalid', 'enriched_candidates': 0})
            continue
        enriched, count = enrich_payload(payload, bank)
        if count:
            write_json(path, enriched)
        total_enriched += count
        file_reports.append({'path': str(path), 'status': 'updated' if count else 'unchanged', 'enriched_candidates': count})

    daily = check_daily_reports()
    report = {
        'status': 'ok',
        'updated_at_utc': datetime.now(UTC).isoformat(),
        'reset_applied': reset,
        'bankroll': bank,
        'bankroll_change': bankroll_change,
        'candidate_files': file_reports,
        'enriched_candidates_total': total_enriched,
        'daily_report_control': daily,
        'controls': {
            'stake_percent_written': True,
            'open_exposure_recomputed_from_pending_bets': not reset,
            'bankroll_reset_to_1000_supported_by_env': 'BANKROLL_RESET_NOW=true',
        },
        'notes': [
            'Each candidate receives stake_pct/recommended_stake_pct and recommended_stake_text before controlled fallback publishing.',
            'Bankroll state is normalized before persistent state sync, so open exposure and available balance stay consistent.',
            'Daily-report files are checked for corrupted rows and impossible negative totals.',
        ],
    }
    write_json(OUT, report)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
