from __future__ import annotations

import hashlib
import json
import os
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib import parse, request

UTC = timezone.utc
ROOT = Path('.').resolve()
OUT_JSON = ROOT / '.data' / 'exports' / 'latest-no-pick-diagnostics.json'
OUT_TXT = ROOT / '.data' / 'exports' / 'latest-no-pick-diagnostics.txt'
SENT_STATE = ROOT / '.data' / 'no-pick-diagnostics-sent.json'


def env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw in (None, ''):
        return default
    return str(raw).strip().lower() in {'1', 'true', 'yes', 'on'}


def env_int(name: str, default: int = 0) -> int:
    try:
        raw = os.getenv(name)
        if raw in (None, ''):
            return default
        return int(float(str(raw).strip()))
    except Exception:
        return default


def as_int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ''):
            return default
        return int(float(value))
    except Exception:
        return default


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


def latest_existing(paths: list[str | Path]) -> dict[str, Any]:
    for path in paths:
        payload = load_json(path, None)
        if isinstance(payload, dict) and payload:
            return payload
    return {}


def debug_last_run() -> dict[str, Any]:
    payload = load_json(ROOT / '.logs' / 'debug-last-run.json', {})
    if isinstance(payload, dict) and isinstance(payload.get('summary'), dict):
        return payload
    if isinstance(payload, dict) and payload:
        return {'summary': payload}
    return {}


def fallback_report() -> dict[str, Any]:
    return latest_existing([
        ROOT / 'artifacts' / 'controlled-fallback-report.json',
        ROOT / '.data' / 'exports' / 'latest-controlled-fallback-report.json',
    ])


def volume_report() -> dict[str, Any]:
    return latest_existing([
        ROOT / '.data' / 'exports' / 'latest-volume-governor.json',
        ROOT / '.data' / 'exports' / 'latest-daily-top5-publish-policy.json',
    ])


def reason_groups(reasons: dict[str, int]) -> dict[str, int]:
    groups: dict[str, int] = {
        'value_ev': 0,
        'confidence_quality': 0,
        'book_support': 0,
        'missing_context': 0,
        'market_signal_guard': 0,
        'xg_sanity': 0,
        'time_window': 0,
        'other': 0,
    }
    for reason, count in reasons.items():
        text = str(reason).lower()
        c = as_int(count)
        if 'ev_below' in text or 'edge_below' in text or 'negative_value' in text or 'canonical_negative' in text:
            groups['value_ev'] += c
        elif 'confidence' in text or 'quality' in text:
            groups['confidence_quality'] += c
        elif 'book' in text or 'source' in text or 'publish_books' in text:
            groups['book_support'] += c
        elif 'missing_context' in text:
            groups['missing_context'] += c
        elif 'market_derived' in text or 'simple_market_signal' in text:
            groups['market_signal_guard'] += c
        elif 'xg' in text or 'btts_direction' in text or 'dnb_' in text:
            groups['xg_sanity'] += c
        elif 'time' in text or 'kickoff' in text or 'started' in text:
            groups['time_window'] += c
        else:
            groups['other'] += c
    return groups


def extract_fallback_evaluated(report: dict[str, Any]) -> list[dict[str, Any]]:
    for key in ('evaluated', 'candidates', 'checked_candidates', 'rejected_candidates'):
        rows = report.get(key)
        if isinstance(rows, list):
            return [row for row in rows if isinstance(row, dict)]
    return []


def classify_volume(volume: dict[str, Any]) -> dict[str, Any]:
    applied = volume.get('applied_env') if isinstance(volume.get('applied_env'), dict) else {}
    counts = volume.get('counts') if isinstance(volume.get('counts'), dict) else {}
    existing = as_int(volume.get('existing_picks_today'), as_int(applied.get('VOLUME_EXISTING_PICKS_TODAY'), as_int(counts.get('effective_today_picks'))))
    hard = as_int(applied.get('VOLUME_DAILY_HARD_CAP_PICKS'), as_int(volume.get('hard_cap_picks'), 0))
    soft = as_int(applied.get('VOLUME_DAILY_SOFT_CAP_PICKS'), as_int(volume.get('soft_cap_picks'), 0))
    max_picks = as_int(applied.get('MAX_PICKS_PER_RUN'), env_int('MAX_PICKS_PER_RUN', 0))
    fallback_enabled = str(applied.get('CONTROLLED_FALLBACK_ENABLED', os.getenv('CONTROLLED_FALLBACK_ENABLED', ''))).lower()
    hard_cap_reached = bool(hard and existing >= hard)
    zero_limit = max_picks <= 0
    return {
        'existing_today_picks': existing,
        'soft_cap_picks': soft,
        'hard_cap_picks': hard,
        'max_picks_per_run': max_picks,
        'controlled_fallback_enabled': fallback_enabled,
        'hard_cap_reached': hard_cap_reached,
        'zero_limit': zero_limit,
        'reasons': volume.get('decision_reasons') or [volume.get('reason')] if volume.get('reason') else volume.get('decision_reasons'),
    }


def build_payload() -> dict[str, Any]:
    debug = debug_last_run()
    summary = debug.get('summary') if isinstance(debug.get('summary'), dict) else debug
    fallback = fallback_report()
    volume = volume_report()
    evaluated = extract_fallback_evaluated(fallback)
    rejections = summary.get('rejections') if isinstance(summary.get('rejections'), dict) else {}
    reason_counts = {str(k): as_int(v) for k, v in rejections.items()}
    top_reasons = dict(Counter(reason_counts).most_common(15))
    groups = reason_groups(reason_counts)
    volume_status = classify_volume(volume)

    provider_diag = summary.get('provider_diagnostics') if isinstance(summary.get('provider_diagnostics'), dict) else {}
    diag_summary = provider_diag.get('summary') if isinstance(provider_diag.get('summary'), dict) else {}
    context_combos = diag_summary.get('context_source_combinations') if isinstance(diag_summary.get('context_source_combinations'), dict) else {}
    offer_combos = diag_summary.get('offer_source_combinations') if isinstance(diag_summary.get('offer_source_combinations'), dict) else {}

    candidates_before_quality = as_int(summary.get('candidates_before_quality'))
    raw_candidates = as_int(summary.get('candidates_raw'))
    publishable = as_int(summary.get('candidates_publishable'))
    selected_count = as_int(fallback.get('selected_count'))
    rescue_checked = as_int(fallback.get('rescue_candidates_checked') or fallback.get('checked') or len(evaluated))

    primary_cause = 'unknown'
    if volume_status['hard_cap_reached'] or volume_status['zero_limit']:
        primary_cause = 'daily_hard_cap_or_zero_run_limit'
    elif candidates_before_quality <= 0:
        primary_cause = 'no_candidates_before_quality'
    elif raw_candidates <= 0:
        primary_cause = 'all_candidates_rejected_by_quality'
    elif publishable <= 0:
        primary_cause = 'candidates_not_publishable'

    report_gap = ''
    if candidates_before_quality > 0 and not evaluated and (volume_status['hard_cap_reached'] or volume_status['zero_limit']):
        report_gap = 'fallback_not_evaluated_due_to_daily_cap'
    elif candidates_before_quality > 0 and not evaluated:
        report_gap = 'fallback_not_evaluated_despite_model_candidates'

    return {
        'created_at_utc': datetime.now(UTC).isoformat(),
        'primary_cause': primary_cause,
        'report_gap': report_gap,
        'summary_counts': {
            'matches_seen': as_int(summary.get('matches_seen')),
            'matches_before_publish_window': as_int(summary.get('matches_before_publish_window')),
            'matches_with_offers': as_int(summary.get('matches_with_offers')),
            'contexts_built': as_int(summary.get('contexts_built')),
            'candidates_before_quality': candidates_before_quality,
            'candidates_raw': raw_candidates,
            'candidates_publishable': publishable,
            'rescue_checked': rescue_checked,
            'fallback_evaluated': len(evaluated),
            'fallback_selected': selected_count,
        },
        'volume_status': volume_status,
        'top_rejections': top_reasons,
        'rejection_groups': groups,
        'coverage_snapshot': {
            'context_source_combinations': context_combos,
            'offer_source_combinations': offer_combos,
            'raw_candidates_with_derived_market_signal': as_int(diag_summary.get('raw_candidates_with_derived_market_signal')),
            'matches_with_any_offer_source': as_int(diag_summary.get('matches_with_any_offer_source')),
            'matches_with_any_context_source': as_int(diag_summary.get('matches_with_any_context_source')),
        },
        'interpretation': interpretation(primary_cause, report_gap, groups, volume_status),
    }


def interpretation(primary_cause: str, report_gap: str, groups: dict[str, int], volume_status: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    if primary_cause == 'daily_hard_cap_or_zero_run_limit':
        lines.append(
            f"Резерв и публикация заблокированы дневным лимитом: {volume_status.get('existing_today_picks')}/{volume_status.get('hard_cap_picks')} picks, MAX_PICKS_PER_RUN={volume_status.get('max_picks_per_run')}."
        )
    if report_gap == 'fallback_not_evaluated_due_to_daily_cap':
        lines.append('Пограничные кандидаты не оценивались fallback-скриптом из-за лимита; фраза "не найдено" в обычном отчёте означает отсутствие fallback-evaluation, а не отсутствие всех near-miss сигналов.')
    if groups.get('value_ev', 0) > 0:
        lines.append(f"Главный модельный фильтр — value/EV: {groups['value_ev']} отсечений. Рынок не дал достаточного перевеса над выбранным коэффициентом.")
    if groups.get('market_signal_guard', 0) > 0:
        lines.append(f"Market-derived слой почти не помог: {groups['market_signal_guard']} отсечений по готовности/качеству рыночного сигнала.")
    if groups.get('confidence_quality', 0) > 0:
        lines.append(f"Часть вариантов просела по confidence/quality: {groups['confidence_quality']} отсечений.")
    if groups.get('book_support', 0) > 0:
        lines.append(f"Подтверждение линиями слабое: {groups['book_support']} отсечений по books/sources/publish guard.")
    if groups.get('missing_context', 0) > 0:
        lines.append(f"Есть контекстные дыры по отдельным рынкам: {groups['missing_context']} missing-context отсечений.")
    if not lines:
        lines.append('Причина не классифицирована автоматически; смотри top_rejections и debug-last-run.')
    return lines


def render(payload: dict[str, Any]) -> str:
    counts = payload.get('summary_counts') or {}
    volume = payload.get('volume_status') or {}
    groups = payload.get('rejection_groups') or {}
    top = payload.get('top_rejections') or {}
    coverage = payload.get('coverage_snapshot') or {}
    lines = [
        '🧪 Диагностика no-pick run',
        '',
        '📌 Главная причина',
    ]
    for item in payload.get('interpretation') or []:
        lines.append(f'• {item}')
    lines.extend([
        '',
        '🔢 Воронка кандидатов',
        f"• Матчи: {counts.get('matches_seen', 0)} / дневной слой {counts.get('matches_before_publish_window', 0)}",
        f"• Линии: {counts.get('matches_with_offers', 0)} | контекст: {counts.get('contexts_built', 0)}",
        f"• До quality: {counts.get('candidates_before_quality', 0)} | raw после quality: {counts.get('candidates_raw', 0)} | publishable: {counts.get('candidates_publishable', 0)}",
        f"• Резерв: checked {counts.get('rescue_checked', 0)} | evaluated {counts.get('fallback_evaluated', 0)} | selected {counts.get('fallback_selected', 0)}",
        '',
        '🧯 Лимит публикаций',
        f"• Today picks: {volume.get('existing_today_picks', 0)}/{volume.get('hard_cap_picks', 0)} | MAX_PICKS_PER_RUN={volume.get('max_picks_per_run', 0)} | fallback={volume.get('controlled_fallback_enabled', '')}",
        '',
        '🚫 Группы отсечений',
    ])
    for key, label in (
        ('value_ev', 'value/EV'),
        ('market_signal_guard', 'market-derived signal'),
        ('confidence_quality', 'confidence/quality'),
        ('book_support', 'books/sources'),
        ('missing_context', 'missing context'),
        ('xg_sanity', 'xG/sanity'),
    ):
        value = as_int(groups.get(key))
        if value:
            lines.append(f'• {label}: {value}')
    if top:
        lines.append('')
        lines.append('🔎 Top raw reasons')
        for reason, count in list(top.items())[:8]:
            lines.append(f'• {reason}: {count}')
    lines.extend([
        '',
        '📡 Coverage snapshot',
        f"• offer combos: {coverage.get('offer_source_combinations')}",
        f"• context combos: {coverage.get('context_source_combinations')}",
        f"• market-derived raw: {coverage.get('raw_candidates_with_derived_market_signal', 0)}",
    ])
    return '\n'.join(lines).strip() + '\n'


def should_send(text: str) -> bool:
    if not env_bool('NO_PICK_DIAGNOSTICS_SEND_TELEGRAM', False):
        return False
    token = os.getenv('TELEGRAM_TOKEN')
    chat_id = os.getenv('TELEGRAM_CHAT_ID')
    if not token or not chat_id:
        return False
    interval = max(0, env_int('NO_PICK_DIAGNOSTICS_MIN_INTERVAL_MINUTES', 30))
    digest = hashlib.sha1(text.encode('utf-8')).hexdigest()
    state = load_json(SENT_STATE, {})
    if isinstance(state, dict):
        last_digest = str(state.get('digest') or '')
        last_sent_raw = str(state.get('sent_at_utc') or '')
        try:
            last_sent = datetime.fromisoformat(last_sent_raw.replace('Z', '+00:00'))
            if last_digest == digest and datetime.now(UTC) - last_sent < timedelta(minutes=interval):
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
    url = f'https://api.telegram.org/bot{token}/sendMessage'
    try:
        with request.urlopen(url, data=data, timeout=15) as response:
            ok = 200 <= response.status < 300
    except Exception:
        ok = False
    if ok:
        write_json(SENT_STATE, {'sent_at_utc': datetime.now(UTC).isoformat(), 'digest': hashlib.sha1(text.encode('utf-8')).hexdigest()})
    return ok


def main() -> int:
    payload = build_payload()
    text = render(payload)
    payload['text'] = text
    write_json(OUT_JSON, payload)
    write_text(OUT_TXT, text)
    print(text)
    if should_send(text):
        payload['telegram_sent'] = send_telegram(text)
        write_json(OUT_JSON, payload)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
