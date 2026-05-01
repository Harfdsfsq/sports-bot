from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib import parse, request

ROOT = Path('.').resolve()
UTC = timezone.utc
STATE_PATH = ROOT / '.data' / 'state.json'
AUDIT_PATH = ROOT / '.data' / 'exports' / 'latest-bankroll-control-audit.json'
OUT_JSON = ROOT / '.data' / 'exports' / 'latest-bankroll-report-block.json'
OUT_TXT = ROOT / '.data' / 'exports' / 'latest-bankroll-report-block.txt'
SENT_STATE = ROOT / '.data' / 'bankroll-report-block-sent.json'


def env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == '':
        return default
    return str(raw).strip().lower() in {'1', 'true', 'yes', 'on'}


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


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.rstrip() + '\n', encoding='utf-8')


def as_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ''):
            return default
        return float(value)
    except Exception:
        return default


def run_clean_bankroll_state() -> dict[str, Any]:
    script = ROOT / 'scripts' / 'clean_bankroll_state.py'
    if not script.exists():
        return {'status': 'missing', 'script': str(script)}
    try:
        proc = subprocess.run(
            [sys.executable, str(script)],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=45,
        )
        payload = load_json(ROOT / '.data' / 'exports' / 'latest-bankroll-state-cleanup.json', {})
        return {
            'status': 'ok' if proc.returncode == 0 else 'failed',
            'returncode': proc.returncode,
            'stdout_tail': proc.stdout[-1500:],
            'stderr_tail': proc.stderr[-1500:],
            'report': payload if isinstance(payload, dict) else None,
        }
    except Exception as exc:  # noqa: BLE001
        return {'status': 'failed', 'error': f'{type(exc).__name__}: {exc}'}


def current_bankroll() -> dict[str, Any]:
    # Prefer state after cleanup; audit can be stale from before fallback sync.
    state = load_json(STATE_PATH, {})
    if isinstance(state, dict) and isinstance(state.get('bankroll'), dict):
        return dict(state['bankroll'])
    audit = load_json(AUDIT_PATH, {})
    if isinstance(audit, dict) and isinstance(audit.get('bankroll'), dict):
        return dict(audit['bankroll'])
    return {
        'starting_balance': 1000.0,
        'current_balance': 1000.0,
        'open_exposure': 0.0,
        'available_balance': 1000.0,
        'closed_pnl': 0.0,
        'roi_pct': 0.0,
        'currency': 'units',
    }


def reset_applied() -> bool:
    state = load_json(STATE_PATH, {})
    if isinstance(state, dict) and state.get('bankroll_reset_at_utc'):
        return True
    audit = load_json(AUDIT_PATH, {})
    if isinstance(audit, dict) and 'reset_applied' in audit:
        return bool(audit.get('reset_applied'))
    return False


def candidate_stake_stats() -> dict[str, Any]:
    audit = load_json(AUDIT_PATH, {})
    total = 0
    files = []
    if isinstance(audit, dict):
        total = int(audit.get('enriched_candidates_total') or 0)
        for row in audit.get('candidate_files') or []:
            if isinstance(row, dict):
                files.append({
                    'path': row.get('path'),
                    'status': row.get('status'),
                    'enriched_candidates': row.get('enriched_candidates'),
                })
    return {'enriched_candidates_total': total, 'files': files}


def open_bets_lines(bank: dict[str, Any]) -> list[str]:
    state = load_json(STATE_PATH, {})
    rows = state.get('bets') if isinstance(state, dict) and isinstance(state.get('bets'), list) else []
    current = as_float(bank.get('current_balance'), 1000.0) or 1000.0
    lines: list[str] = []
    for row in rows[:8]:
        if not isinstance(row, dict):
            continue
        stake = as_float(row.get('stake_amount') or row.get('stake') or row.get('risk'))
        if stake <= 0:
            continue
        stake_pct = as_float(row.get('stake_pct') or row.get('recommended_stake_pct'))
        if stake_pct <= 0 and current > 0:
            stake_pct = stake / current * 100.0
        home = str(row.get('home_team') or '').strip()
        away = str(row.get('away_team') or '').strip()
        selection = str(row.get('selection') or '').strip()
        match = f'{home} — {away}'.strip(' —') or str(row.get('match_key') or 'матч')
        tail = f' | {selection}' if selection else ''
        lines.append(f'• {match}{tail}: {stake:.2f} ({stake_pct:.2f}% банка)')
    return lines


def money(value: Any) -> str:
    return f'{as_float(value):.2f}'


def build_text() -> str:
    bank = current_bankroll()
    stake_stats = candidate_stake_stats()
    current = as_float(bank.get('current_balance'))
    open_exposure = as_float(bank.get('open_exposure'))
    available = as_float(bank.get('available_balance'), max(0.0, current - open_exposure))
    starting = as_float(bank.get('starting_balance'), 1000.0)
    closed_pnl = as_float(bank.get('closed_pnl'))
    roi = as_float(bank.get('roi_pct'))
    reset = reset_applied()
    exposure_pct = open_exposure / current * 100.0 if current > 0 else 0.0
    available_pct = available / current * 100.0 if current > 0 else 0.0
    open_lines = open_bets_lines(bank)
    open_block = ''
    if open_lines:
        open_block = '\n\n📌 Открытые ставки:\n' + '\n'.join(open_lines)
    return (
        '💼 Контроль банка / риск\n\n'
        f'• Банк: {money(current)} / старт {money(starting)}\n'
        f'• P&L: {closed_pnl:+.2f} | ROI: {roi:+.2f}%\n'
        f'• Открытая экспозиция: {money(open_exposure)} ({exposure_pct:.2f}% банка)\n'
        f'• Доступно: {money(available)} ({available_pct:.2f}% банка)\n'
        f'• Stake %: записан в кандидаты ({stake_stats["enriched_candidates_total"]} enriched)\n'
        f'• reset_applied: {str(reset).lower()}'
        f'{open_block}\n\n'
        'Правило: в прогнозе ставка должна показываться как сумма и процент от текущего банка.'
    )


def send_telegram(text: str) -> dict[str, Any]:
    if not env_bool('BANKROLL_REPORT_SEND_TELEGRAM', True):
        return {'status': 'skipped', 'reason': 'disabled'}
    token = os.getenv('TELEGRAM_TOKEN') or os.getenv('TELEGRAM_BOT_TOKEN')
    chat_id = os.getenv('TELEGRAM_CHAT_ID')
    dry_run = env_bool('PUBLISH_DRY_RUN', False)
    if dry_run:
        return {'status': 'dry_run'}
    if not token or not chat_id:
        return {'status': 'skipped', 'reason': 'telegram_credentials_missing'}
    fingerprint = hashlib.sha1(text.encode('utf-8')).hexdigest()
    sent_state = load_json(SENT_STATE, {})
    if isinstance(sent_state, dict) and sent_state.get('last_hash') == fingerprint and not env_bool('BANKROLL_REPORT_FORCE_SEND', False):
        return {'status': 'skipped', 'reason': 'unchanged', 'hash': fingerprint}
    url = f'https://api.telegram.org/bot{token}/sendMessage'
    data = parse.urlencode({'chat_id': chat_id, 'text': text, 'disable_web_page_preview': 'true'}).encode('utf-8')
    req = request.Request(url, data=data, method='POST')
    try:
        with request.urlopen(req, timeout=20) as response:
            body = response.read().decode('utf-8', 'replace')[:1000]
        write_json(SENT_STATE, {'last_hash': fingerprint, 'sent_at': datetime.now(UTC).isoformat()})
        return {'status': 'sent', 'http_status': getattr(response, 'status', None), 'hash': fingerprint, 'body_preview': body}
    except Exception as exc:  # noqa: BLE001
        return {'status': 'failed', 'error': f'{type(exc).__name__}: {exc}', 'hash': fingerprint}


def main() -> int:
    cleanup = run_clean_bankroll_state()
    text = build_text()
    write_text(OUT_TXT, text)
    send_result = send_telegram(text)
    payload = {
        'status': 'ok',
        'created_at_utc': datetime.now(UTC).isoformat(),
        'cleanup': cleanup,
        'text': text,
        'bankroll': current_bankroll(),
        'reset_applied': reset_applied(),
        'candidate_stake_stats': candidate_stake_stats(),
        'telegram': send_result,
    }
    write_json(OUT_JSON, payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
