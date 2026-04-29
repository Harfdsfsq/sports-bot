from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib import parse, request

UTC = timezone.utc
ROOT = Path('.').resolve()
OUT_JSON = ROOT / '.data' / 'exports' / 'latest-daily-best5-no-pick-note.json'
OUT_TXT = ROOT / '.data' / 'exports' / 'latest-daily-best5-no-pick-note.txt'
SENT_STATE = ROOT / '.data' / 'daily-best5-no-pick-note-sent.json'


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


def as_int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ''):
            return default
        return int(float(value))
    except Exception:
        return default


def as_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ''):
            return default
        return float(value)
    except Exception:
        return default


def env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw in (None, ''):
        return default
    return str(raw).strip().lower() in {'1', 'true', 'yes', 'on'}


def latest_existing(paths: list[str | Path]) -> dict[str, Any]:
    for path in paths:
        payload = load_json(path, None)
        if isinstance(payload, dict) and payload:
            return payload
    return {}


def fallback_report() -> dict[str, Any]:
    return latest_existing([
        ROOT / 'artifacts' / 'controlled-fallback-report.json',
        ROOT / '.data' / 'exports' / 'latest-controlled-fallback-report.json',
    ])


def governor_report() -> dict[str, Any]:
    return latest_existing([
        ROOT / '.data' / 'exports' / 'latest-daily-best5-governor.json',
        ROOT / '.data' / 'exports' / 'latest-volume-governor.json',
        ROOT / '.data' / 'exports' / 'latest-daily-top5-publish-policy.json',
    ])


def unwrap_candidate(row: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    candidate = row.get('candidate') if isinstance(row.get('candidate'), dict) else row
    metrics = row.get('metrics') if isinstance(row.get('metrics'), dict) else {}
    if not metrics and isinstance(candidate.get('metrics'), dict):
        metrics = candidate.get('metrics')
    reasons = row.get('reject_reasons') or row.get('reasons') or candidate.get('reject_reasons') or candidate.get('reasons') or []
    if isinstance(reasons, str):
        reasons = [reasons]
    return candidate, metrics if isinstance(metrics, dict) else {}, [str(x) for x in reasons if str(x).strip()]


def evaluated_candidates(report: dict[str, Any]) -> list[dict[str, Any]]:
    for key in ('evaluated', 'candidates', 'checked_candidates', 'rejected_candidates'):
        rows = report.get(key)
        if isinstance(rows, list):
            return [row for row in rows if isinstance(row, dict)]
    return []


def metric(candidate: dict[str, Any], metrics: dict[str, Any], *keys: str, default: float = 0.0) -> float:
    for key in keys:
        if key in metrics:
            return as_float(metrics.get(key), default)
        if key in candidate:
            return as_float(candidate.get(key), default)
    return default


def best_near_miss(report: dict[str, Any]) -> dict[str, Any] | None:
    rows = []
    for row in evaluated_candidates(report):
        candidate, metrics, reasons = unwrap_candidate(row)
        ev = metric(candidate, metrics, 'canonical_ev_pct', 'ev_pct')
        edge = metric(candidate, metrics, 'canonical_edge_pp', 'edge_pp')
        confidence = metric(candidate, metrics, 'confidence')
        quality = metric(candidate, metrics, 'quality_score')
        if ev <= 0 and edge <= 0:
            continue
        rows.append({
            'candidate': candidate,
            'metrics': metrics,
            'reasons': reasons,
            'ev': ev,
            'edge': edge,
            'confidence': confidence,
            'quality': quality,
            'score': (ev, edge, confidence, quality),
        })
    if not rows:
        return None
    rows.sort(key=lambda x: x['score'], reverse=True)
    return rows[0]


def short_reason(reason: str) -> str:
    mapping = {
        'canonical_negative_value': 'отрицательная value',
        'tier_a_books_below_min': 'мало линий для A',
        'tier_b_books_below_min': 'мало линий для B',
        'tier_a_confidence_below_min': 'уверенность ниже A',
        'tier_b_confidence_below_min': 'уверенность ниже B',
        'tier_a_quality_below_min': 'качество ниже A',
        'tier_b_quality_below_min': 'качество ниже B',
        'tier_a_proxy_quality_not_allowed': 'A не принимает proxy-качество',
        'tier_b_canonical_edge_below_min': 'edge ниже B',
        'tier_b_canonical_ev_below_min': 'EV ниже B',
        'telegram_publish_books_guard': 'мало линий для Telegram',
        'proxy_single_book_guard': 'proxy только с одной линией',
        'final_edge_below_min': 'финальный edge ниже минимума',
        'final_ev_below_min': 'финальный EV ниже минимума',
    }
    return mapping.get(reason, reason.replace('_', ' '))


def format_match(candidate: dict[str, Any]) -> str:
    home = str(candidate.get('home_team') or candidate.get('home') or '').strip()
    away = str(candidate.get('away_team') or candidate.get('away') or '').strip()
    if home and away:
        return f'{home} — {away}'
    return str(candidate.get('match_key') or 'матч')


def build_note() -> tuple[dict[str, Any], str]:
    gov = governor_report()
    fallback = fallback_report()
    selected = as_int(fallback.get('selected_count'))
    published = bool(fallback.get('published') or fallback.get('telegram_sent') or selected > 0)
    stage = str(gov.get('stage') or gov.get('target_governor', {}).get('stage') or os.getenv('VOLUME_POLICY_STAGE') or '')
    existing = as_int(gov.get('existing_today_picks'), as_int(os.getenv('VOLUME_EXISTING_PICKS_TODAY')))
    target = as_int(gov.get('target_picks'), as_int(os.getenv('VOLUME_DAILY_TARGET_PICKS'), 5))
    allowed = as_int(gov.get('allowed_this_run'), as_int(os.getenv('MAX_PICKS_PER_RUN'), 0))
    policy = gov.get('policy') if isinstance(gov.get('policy'), dict) else {}
    thresholds = {
        'confidence': policy.get('tier_b_min_confidence') or os.getenv('CONTROLLED_FALLBACK_TIER_B_MIN_CONFIDENCE'),
        'quality': policy.get('tier_b_min_quality') or os.getenv('CONTROLLED_FALLBACK_TIER_B_MIN_QUALITY'),
        'edge': policy.get('tier_b_min_edge_pp') or os.getenv('CONTROLLED_FALLBACK_TIER_B_MIN_EDGE_PP'),
        'ev': policy.get('tier_b_min_ev_pct') or os.getenv('CONTROLLED_FALLBACK_TIER_B_MIN_EV_PCT'),
    }
    eligible_stage = stage in {'after_target_elite_only', 'ahead_elite_only'}
    should_note = (not published) and eligible_stage and existing >= target

    near = best_near_miss(fallback)
    lines = []
    if should_note:
        lines.append('🎯 Daily Best-5 Governor')
        lines.append('')
        lines.append(f'Сегодня уже опубликовано {existing}/{target} прогнозов. Система выше дневной цели, поэтому новые ставки проходят только в elite-only режиме.')
        lines.append(f'Текущий режим: {stage} | максимум за run: {allowed}.')
        lines.append(f'Elite-пороги B: confidence ≥ {thresholds["confidence"]}, quality ≥ {thresholds["quality"]}, edge ≥ {thresholds["edge"]} п.п., EV ≥ {thresholds["ev"]}%.')
        if near:
            c = near['candidate']
            reasons = '; '.join(short_reason(x) for x in near['reasons'][:4]) or 'не прошёл elite-фильтры'
            lines.append('')
            lines.append('Ближайший кандидат:')
            lines.append(f'• {format_match(c)}')
            lines.append(f'• {c.get("selection") or "ставка"} @{c.get("odds") or near["metrics"].get("odds") or "н/д"}')
            lines.append(f'• EV {near["ev"]:+.1f}% | edge {near["edge"]:+.1f} п.п. | confidence {near["confidence"]:.1f}% | quality {near["quality"]:.1f}')
            lines.append(f'• Не опубликован: {reasons}')
        lines.append('')
        lines.append('Это не ошибка и не отключение резерва: резерв проверяет кандидатов, но после дневной цели публикует только исключительные value-сигналы.')
    payload = {
        'created_at_utc': datetime.now(UTC).isoformat(),
        'should_note': should_note,
        'published': published,
        'stage': stage,
        'existing_today_picks': existing,
        'target_picks': target,
        'allowed_this_run': allowed,
        'thresholds': thresholds,
        'near_miss': near,
    }
    return payload, '\n'.join(lines).strip() + ('\n' if lines else '')


def should_send(text: str) -> bool:
    if not text:
        return False
    if not env_bool('DAILY_BEST5_NO_PICK_NOTE_SEND_TELEGRAM', True):
        return False
    if not os.getenv('TELEGRAM_TOKEN') or not os.getenv('TELEGRAM_CHAT_ID'):
        return False
    digest = hashlib.sha1(text.encode('utf-8')).hexdigest()
    state = load_json(SENT_STATE, {})
    if isinstance(state, dict) and state.get('digest') == digest:
        last = state.get('sent_at_utc')
        try:
            last_dt = datetime.fromisoformat(str(last).replace('Z', '+00:00'))
            if datetime.now(UTC) - last_dt < timedelta(minutes=90):
                return False
        except Exception:
            pass
    return True


def send_telegram(text: str) -> bool:
    token = os.getenv('TELEGRAM_TOKEN')
    chat_id = os.getenv('TELEGRAM_CHAT_ID')
    if not token or not chat_id:
        return False
    data = parse.urlencode({'chat_id': chat_id, 'text': text[:3900], 'disable_web_page_preview': 'true'}).encode('utf-8')
    try:
        with request.urlopen(f'https://api.telegram.org/bot{token}/sendMessage', data=data, timeout=20) as resp:
            ok = 200 <= resp.status < 300
    except Exception:
        ok = False
    if ok:
        write_json(SENT_STATE, {'sent_at_utc': datetime.now(UTC).isoformat(), 'digest': hashlib.sha1(text.encode('utf-8')).hexdigest()})
    return ok


def main() -> int:
    payload, text = build_note()
    payload['text'] = text
    write_json(OUT_JSON, payload)
    write_text(OUT_TXT, text)
    print(text or json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    if payload.get('should_note') and should_send(text):
        payload['telegram_sent'] = send_telegram(text)
        write_json(OUT_JSON, payload)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
