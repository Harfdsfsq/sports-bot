from __future__ import annotations

import hashlib
import json
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib import parse, request

UTC = timezone.utc
EXPORT = Path('.data/exports')
OUT_JSON = EXPORT / 'latest-detailed-run-report.json'
OUT_TXT = EXPORT / 'latest-detailed-run-report.txt'
SENT_STATE = Path('.data/detailed-run-report-sent.json')


def _load(path: str | Path, default: Any) -> Any:
    try:
        p = Path(path)
        if p.exists() and p.stat().st_size > 0:
            return json.loads(p.read_text(encoding='utf-8', errors='replace'))
    except Exception:
        pass
    return default


def _write(path: str | Path, data: Any) -> None:
    p = Path(path); p.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, str):
        p.write_text(data, encoding='utf-8')
    else:
        p.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')


def _int(v: Any) -> int:
    try:
        if isinstance(v, (list, tuple, set, dict)):
            return len(v)
        return int(float(str(v).replace(',', '.'))) if v not in (None, '') else 0
    except Exception:
        return 0


def _float(v: Any) -> float:
    try:
        return float(str(v).replace(',', '.')) if v not in (None, '') else 0.0
    except Exception:
        return 0.0


def _rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if isinstance(payload, dict):
        for key in ('evaluated', 'candidates', 'checked_candidates', 'rejected_candidates', 'near_misses'):
            if isinstance(payload.get(key), list):
                return [x for x in payload[key] if isinstance(x, dict)]
    return []


def _unwrap(row: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    c = row.get('candidate') if isinstance(row.get('candidate'), dict) else row
    m = row.get('metrics') if isinstance(row.get('metrics'), dict) else c.get('metrics') if isinstance(c.get('metrics'), dict) else {}
    rs = row.get('reject_reasons') or row.get('reasons') or row.get('hard_reject_reasons') or c.get('reject_reasons') or c.get('reasons') or []
    if isinstance(rs, str):
        rs = [rs]
    return c, m, [str(x) for x in rs if str(x).strip()]


def _metric(c: dict[str, Any], m: dict[str, Any], *keys: str) -> float:
    for k in keys:
        if m.get(k) not in (None, ''):
            return _float(m.get(k))
        if c.get(k) not in (None, ''):
            return _float(c.get(k))
    return 0.0


def _reason_counts(report: dict[str, Any], rows: list[dict[str, Any]]) -> Counter:
    out = Counter()
    for key in ('reject_reasons', 'reason_counts', 'rejection_reasons'):
        raw = report.get(key)
        if isinstance(raw, dict):
            for k, v in raw.items():
                out[str(k)] += _int(v)
    if not out:
        for row in rows:
            _, _, rs = _unwrap(row); out.update(rs)
    return out


def _patch_lines() -> list[str]:
    out: list[str] = []
    specs = [
        ('confirmation bridge', 'latest-controlled-fallback-confirmation-bridge.json'),
        ('quality shadow', 'latest-quality-shadow-diagnostics.json'),
        ('current price patch', 'latest-current-price-recheck-value.json'),
        ('zero-raw recovery', 'latest-zero-raw-candidate-recovery.json'),
        ('match-window recovery', 'latest-runtime-match-window-recovery.json'),
        ('evidence/integrity patch', 'latest-fallback-evidence-integrity-runtime-patch.json'),
        ('provider targeting', 'latest-fallback-provider-enrichment-targets.json'),
    ]
    for label, name in specs:
        p = _load(EXPORT / name, {})
        if isinstance(p, dict) and p:
            bits = [str(p.get('status') or p.get('version') or 'ok')]
            for k in ('recovered_matches', 'recovered_candidates', 'patched_odds_source_metrics', 'patched_hard_reject_inputs', 'market_probability_guard_softened_without_real_prices', 'fallback_evaluated'):
                if p.get(k) not in (None, ''):
                    bits.append(f'{k} {p.get(k)}')
            out.append(f'• {label}: ' + ' | '.join(bits[:5]))
    return out


def _provider_lines(debug: dict[str, Any]) -> list[str]:
    summary = (((debug.get('provider_diagnostics') or {}).get('summary') or {}) if isinstance(debug.get('provider_diagnostics'), dict) else {})
    providers = summary.get('providers') if isinstance(summary.get('providers'), dict) else {}
    out: list[str] = []
    for name in ('odds_api_io', 'bzzoiro', 'sstats', 'football_data', 'thesportsdb', 'sportlogic', 'openfootball'):
        row = providers.get(name) if isinstance(providers.get(name), dict) else {}
        stats = row.get('stats') if isinstance(row.get('stats'), dict) else {}
        if isinstance(stats.get('stats'), dict): stats = stats.get('stats')
        if row or stats:
            out.append(f"• {name}: data {_int(row.get('matches_with_data'))}/{_int(row.get('items_total'))}, req {stats.get('requests', 0)}, err {stats.get('response_errors', 0)}")
    return out


def _inventory_lines() -> list[str]:
    inv = _load(EXPORT / 'latest-day-inventory-summary.json', {})
    counts = inv.get('counts') if isinstance(inv, dict) and isinstance(inv.get('counts'), dict) else {}
    if not counts:
        return []
    total = _int(counts.get('matches_total'))
    return [
        '📦 Дневной inventory',
        f"• Матчей всего: {total}",
        f"• С линиями: {_int(counts.get('matches_with_odds'))}/{total}",
        f"• С контекстом: {_int(counts.get('matches_with_context'))}/{total}",
        f"• Готово к модели: {_int(counts.get('matches_ready_for_model'))}/{total}",
        f"• Ближайшие 6 часов: {_int(counts.get('matches_next_6h_ready'))}/{_int(counts.get('matches_next_6h'))} готово",
    ]


def build() -> dict[str, Any]:
    debug = _load('.logs/debug-last-run.json', {})
    run = _load(EXPORT / 'latest-run-summary.json', {})
    fallback = _load(EXPORT / 'latest-controlled-fallback-report.json', {}) or _load('artifacts/controlled-fallback-report.json', {})
    rows = _rows(fallback)
    reasons = _reason_counts(fallback if isinstance(fallback, dict) else {}, rows)
    summary = debug.get('summary') if isinstance(debug, dict) and isinstance(debug.get('summary'), dict) else run.get('summary') if isinstance(run, dict) and isinstance(run.get('summary'), dict) else {}
    near = []
    for row in rows[:12]:
        c, m, rs = _unwrap(row)
        near.append({'candidate': c, 'metrics': m, 'reasons': rs, 'score': [_metric(c,m,'canonical_ev_pct','ev_pct'), _metric(c,m,'canonical_edge_pp','edge_pp'), _metric(c,m,'quality_score','quality')]})
    near.sort(key=lambda x: x['score'], reverse=True)
    return {'created_at': datetime.now(UTC).isoformat(), 'summary': summary, 'candidate_counts': {'evaluated': len(rows), 'rescue_checked': _int((fallback or {}).get('rescue_candidates_checked') if isinstance(fallback, dict) else len(rows)), 'selected_count': _int((fallback or {}).get('selected_count') if isinstance(fallback, dict) else 0)}, 'reason_counts': dict(reasons.most_common(12)), 'near_misses': near[:8], 'patch_lines': _patch_lines(), 'provider_work_lines': _provider_lines(debug if isinstance(debug, dict) else {}), 'coverage_pipeline_lines': _inventory_lines(), 'published': bool(isinstance(fallback, dict) and (fallback.get('published') or fallback.get('selected_count')))}


def render(payload: dict[str, Any]) -> str:
    lines = ['🧾 Подробный отчёт run — ' + ('прогноз опубликован' if payload.get('published') else 'прогнозов нет'), '']
    if payload.get('coverage_pipeline_lines'):
        lines += payload['coverage_pipeline_lines'] + ['']
    s = payload.get('summary') or {}; c = payload.get('candidate_counts') or {}
    lines += ['⚙️ Что сделал скрипт', f"• Матчи: {_int(s.get('matches_seen'))} | с линиями: {_int(s.get('matches_with_offers'))} | контекстов: {_int(s.get('contexts_built'))}", f"• Кандидаты: raw {_int(s.get('candidates_raw'))} | до качества {_int(s.get('candidates_before_quality'))} | publishable {_int(s.get('candidates_publishable'))}", f"• Резерв проверил: {c.get('rescue_checked', 0)} | оценено в отчёте: {c.get('evaluated', 0)} | выбрано: {c.get('selected_count', 0)}", '']
    if payload.get('patch_lines'):
        lines += ['🧩 Диагностика патчей'] + payload['patch_lines'] + ['']
    if payload.get('provider_work_lines'):
        lines += ['📡 Источники / фактическая работа'] + payload['provider_work_lines'] + ['']
    reasons = Counter(payload.get('reason_counts') or {})
    if reasons:
        total = sum(reasons.values()) or 1
        lines.append('🚫 Почему не прошли')
        for r, n in reasons.most_common(10):
            lines.append(f'• {r} — {n} ({n/total*100:.0f}%)')
        lines.append('')
    near = payload.get('near_misses') or []
    if near:
        lines.append('⚠️ Пограничные кандидаты')
        for i, item in enumerate(near[:8], 1):
            cand = item.get('candidate') or {}; m = item.get('metrics') or {}; rs = item.get('reasons') or []
            home = cand.get('home_team_ru') or cand.get('home_team') or cand.get('home') or 'н/д'
            away = cand.get('away_team_ru') or cand.get('away_team') or cand.get('away') or 'н/д'
            sel = cand.get('selection') or cand.get('market') or 'н/д'
            odds = _metric(cand, m, 'odds', 'selected_odds')
            lines.append(f"{i}. {home} — {away} | {sel} @{odds:.2f}")
            lines.append(f"   • EV {_metric(cand,m,'canonical_ev_pct','ev_pct'):+.1f}% | edge {_metric(cand,m,'canonical_edge_pp','edge_pp'):+.1f} п.п. | q {_metric(cand,m,'quality_score','quality'):.1f}")
            if rs: lines.append('   • не прошло: ' + '; '.join(str(x) for x in rs[:5]))
        lines.append('')
    top = reasons.most_common(1)[0][0] if reasons else ''
    lines += ['📌 Вывод']
    if 'price_integrity' in top or 'price integrity' in top:
        lines.append('• Главный блокер — price integrity: выбранная цена выглядит выбросом относительно рынка; новый патч не снимает реальные median/outlier блоки, а только убирает ложный market_probability hard-block без реальных same-side цен.')
    elif 'recheck' in top or 'line' in top:
        lines.append('• Главный блокер — ожидание финального line/current-price recheck перед публикацией.')
    else:
        lines.append('• Прогнозов нет из-за комбинации value, качества, источников и финальных safety-guard’ов.')
    lines += ['', '⚖️ Дисклеймер: аналитика бота не гарантирует результат и не является финансовой рекомендацией.']
    return '\n'.join(lines)


def _split(text: str, limit: int) -> list[str]:
    if len(text) <= limit: return [text]
    parts, cur, size = [], [], 0
    for line in text.splitlines():
        add = len(line) + 1
        if cur and size + add > limit:
            parts.append('\n'.join(cur)); cur=[]; size=0
        cur.append(line); size += add
    if cur: parts.append('\n'.join(cur))
    total = len(parts)
    return [f'🧾 Подробный отчёт run — часть {i}/{total}\n\n{p}' for i, p in enumerate(parts, 1)]


def send(text: str) -> dict[str, Any]:
    token = os.getenv('TELEGRAM_TOKEN') or os.getenv('TELEGRAM_BOT_TOKEN'); chat = os.getenv('TELEGRAM_CHAT_ID')
    if not token or not chat: return {'sent': False, 'reason': 'missing_credentials'}
    h = hashlib.sha1(text.encode('utf-8')).hexdigest(); state = _load(SENT_STATE, {})
    if isinstance(state, dict) and state.get('last_hash') == h and str(os.getenv('DETAILED_RUN_REPORT_FORCE_SEND','')).lower() not in {'1','true','yes','on','force'}:
        return {'sent': False, 'reason': 'unchanged', 'hash': h}
    url = 'https://api.telegram.org/bot' + str(token) + '/sendMessage'
    parts = _split(text, _int(os.getenv('TELEGRAM_MESSAGE_SOFT_LIMIT') or 3900) or 3900)
    try:
        for part in parts:
            data = parse.urlencode({'chat_id': chat, 'text': part, 'disable_web_page_preview': 'true'}).encode('utf-8')
            req = request.Request(url, data=data, method='POST')
            with request.urlopen(req, timeout=20) as resp: resp.read()
    except Exception as exc:
        return {'sent': False, 'reason': 'telegram_send_error', 'error': repr(exc), 'hash': h}
    _write(SENT_STATE, {'last_hash': h, 'sent_at': datetime.now(UTC).isoformat(), 'parts': len(parts)})
    return {'sent': True, 'parts': len(parts), 'hash': h}


def main() -> int:
    payload = build(); text = render(payload); payload['text'] = text
    should_send = str(os.getenv('DETAILED_RUN_REPORT_SEND_TELEGRAM','')).lower() in {'1','true','yes','on','force'}
    payload['telegram'] = send(text) if should_send else {'sent': False, 'reason': 'disabled'}
    _write(OUT_JSON, payload); _write(OUT_TXT, text); print(text)
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
