from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib import parse, request
from zoneinfo import ZoneInfo

UTC = timezone.utc
ROOT = Path('.').resolve()
STATE_PATH = ROOT / '.data' / 'state.json'
OUT_JSON = ROOT / '.data' / 'exports' / 'latest-completed-daily-report-check.json'
OUT_TXT = ROOT / '.data' / 'exports' / 'latest-completed-daily-report-check.txt'
SENT_PATH = ROOT / '.data' / 'completed-daily-report-sent.json'


def app_tz() -> ZoneInfo:
    try:
        return ZoneInfo(os.getenv('APP_TIMEZONE') or os.getenv('TZ') or 'Europe/Moscow')
    except Exception:
        return ZoneInfo('Europe/Moscow')


def load_json(path: str | Path, default: Any) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except Exception:
        return default


def write_json(path: str | Path, payload: Any) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')


def write_text(path: str | Path, text: str) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding='utf-8')


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


def local_date_for_bet(row: dict[str, Any], tz: ZoneInfo) -> str | None:
    for key in ('commence_time', 'start_time', 'kickoff', 'published_at', 'sent_at', 'created_at'):
        dt = parse_dt(row.get(key))
        if dt is not None:
            return dt.astimezone(tz).date().isoformat()
    return None


def as_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ''):
            return default
        return float(value)
    except Exception:
        return default


def completed_status(status: str) -> bool:
    return status in {'won', 'half_won', 'lost', 'half_lost', 'push', 'void', 'cancelled', 'refunded'}


def outcome_emoji(status: str) -> str:
    if status in {'won', 'half_won'}:
        return '✅'
    if status in {'lost', 'half_lost'}:
        return '❌'
    if status == 'push':
        return '➖'
    return '⚪'


def bet_title(row: dict[str, Any]) -> str:
    home = str(row.get('home_team') or row.get('home') or '').strip()
    away = str(row.get('away_team') or row.get('away') or '').strip()
    if home and away:
        return f'{home} — {away}'
    return str(row.get('match_key') or 'Матч')


def selection_text(row: dict[str, Any]) -> str:
    selection = str(row.get('selection') or '').strip()
    odds = row.get('odds')
    if odds not in (None, ''):
        return f'{selection} @{odds}'
    return selection or 'ставка'


def day_rows(report_date: str, tz: ZoneInfo) -> list[dict[str, Any]]:
    state = load_json(STATE_PATH, {})
    rows = []
    if not isinstance(state, dict):
        return rows
    for row in state.get('bets') or []:
        if not isinstance(row, dict):
            continue
        if not bool(row.get('telegram_sent')):
            continue
        if local_date_for_bet(row, tz) != report_date:
            continue
        rows.append(dict(row))
    rows.sort(key=lambda x: str(x.get('commence_time') or x.get('published_at') or ''))
    return rows


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(rows)
    pending = [r for r in rows if str(r.get('status') or '') == 'pending']
    closed = [r for r in rows if completed_status(str(r.get('status') or ''))]
    pnl = 0.0
    stake = 0.0
    wins = losses = pushes = voids = 0
    for row in closed:
        status = str(row.get('status') or '')
        settlement = row.get('settlement') if isinstance(row.get('settlement'), dict) else {}
        pnl += as_float(settlement.get('pnl'), as_float(row.get('pnl')))
        stake += as_float(row.get('stake_amount'))
        if status in {'won', 'half_won'}:
            wins += 1
        elif status in {'lost', 'half_lost'}:
            losses += 1
        elif status == 'push':
            pushes += 1
        else:
            voids += 1
    roi = (pnl / stake * 100.0) if stake > 0 else 0.0
    hit_rate = (wins / max(1, wins + losses) * 100.0) if (wins + losses) > 0 else 0.0
    return {
        'total': total,
        'closed': len(closed),
        'pending': len(pending),
        'wins': wins,
        'losses': losses,
        'pushes': pushes,
        'voids': voids,
        'stake': round(stake, 2),
        'pnl': round(pnl, 2),
        'roi_pct': round(roi, 2),
        'hit_rate_pct': round(hit_rate, 2),
    }


def target_dates(tz: ZoneInfo) -> list[str]:
    local_today = datetime.now(UTC).astimezone(tz).date()
    return [(local_today - timedelta(days=delta)).isoformat() for delta in range(0, 4)]


def already_sent(report_date: str, digest: str) -> bool:
    state = load_json(SENT_PATH, {})
    if not isinstance(state, dict):
        return False
    row = state.get(report_date)
    return isinstance(row, dict) and row.get('digest') == digest and bool(row.get('sent_at_utc'))


def mark_sent(report_date: str, digest: str, telegram_sent: bool) -> None:
    state = load_json(SENT_PATH, {})
    if not isinstance(state, dict):
        state = {}
    state[report_date] = {
        'sent_at_utc': datetime.now(UTC).isoformat(),
        'digest': digest,
        'telegram_sent': bool(telegram_sent),
    }
    write_json(SENT_PATH, state)


def render(report_date: str, rows: list[dict[str, Any]], summary: dict[str, Any]) -> str:
    lines = [
        f'📊 Итоговый отчёт за {report_date}',
        '',
        f"Все прогнозы дня закрыты: {summary['closed']}/{summary['total']}.",
        f"Итог: {summary['wins']}✅ / {summary['losses']}❌ / {summary['pushes']}➖ / {summary['voids']}⚪",
        f"P&L: {summary['pnl']:+.2f} | ROI: {summary['roi_pct']:+.2f}% | Hit rate: {summary['hit_rate_pct']:.1f}%",
        '',
        'Ставки:',
    ]
    for idx, row in enumerate(rows, start=1):
        status = str(row.get('status') or '')
        settlement = row.get('settlement') if isinstance(row.get('settlement'), dict) else {}
        pnl = as_float(settlement.get('pnl'), as_float(row.get('pnl')))
        score = settlement.get('score') or row.get('score') or ''
        score_text = f' | счёт: {score}' if score else ''
        lines.append(f"{idx}. {bet_title(row)}")
        lines.append(f"   {outcome_emoji(status)} {selection_text(row)} | P&L {pnl:+.2f}{score_text}")
    return '\n'.join(lines).strip() + '\n'


def send_telegram(text: str) -> bool:
    token = os.getenv('TELEGRAM_TOKEN')
    chat_id = os.getenv('TELEGRAM_CHAT_ID')
    if not token or not chat_id:
        return False
    data = parse.urlencode({'chat_id': chat_id, 'text': text[:3900], 'disable_web_page_preview': 'true'}).encode('utf-8')
    try:
        with request.urlopen(f'https://api.telegram.org/bot{token}/sendMessage', data=data, timeout=20) as resp:
            return 200 <= resp.status < 300
    except Exception:
        return False


def main() -> int:
    tz = app_tz()
    checks = []
    sent_any = False
    for report_date in target_dates(tz):
        rows = day_rows(report_date, tz)
        summary = summarize(rows)
        ready = summary['total'] > 0 and summary['pending'] == 0 and summary['closed'] == summary['total']
        text = render(report_date, rows, summary) if ready else ''
        digest = hashlib.sha1(text.encode('utf-8')).hexdigest() if text else ''
        sent = False
        skip_reason = None
        if not rows:
            skip_reason = 'no_published_picks_for_date'
        elif summary['pending'] > 0:
            skip_reason = f"pending_picks:{summary['pending']}"
        elif ready and already_sent(report_date, digest):
            skip_reason = 'already_sent'
        elif ready:
            sent = send_telegram(text)
            mark_sent(report_date, digest, sent)
            sent_any = sent_any or sent
        checks.append({
            'date': report_date,
            'ready': ready,
            'telegram_sent': sent,
            'skip_reason': skip_reason,
            'summary': summary,
            'text': text if ready else '',
        })
    payload = {
        'created_at_utc': datetime.now(UTC).isoformat(),
        'policy_version': 'completed-daily-report-v1',
        'telegram_sent_any': sent_any,
        'checks': checks,
    }
    write_json(OUT_JSON, payload)
    write_text(OUT_TXT, '\n\n'.join(c['text'] for c in checks if c.get('text')).strip() + '\n' if any(c.get('text') for c in checks) else '')
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
