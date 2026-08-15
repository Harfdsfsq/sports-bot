from __future__ import annotations

import hashlib
import json
import os
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib import parse, request
from zoneinfo import ZoneInfo

try:
    from app.services.telegram_i18n import normalize_telegram_text, translate_league_name, translate_reject_reason, translate_selection_text, translate_team_name
except Exception:
    def normalize_telegram_text(text: Any) -> str: return str(text or '')
    def translate_league_name(name: Any) -> str: return str(name or '')
    def translate_reject_reason(reason: Any) -> str: return str(reason or '').replace('_', ' ')
    def translate_selection_text(selection: Any, home_team: Any = '', away_team: Any = '') -> str:
        return str(selection or '').replace('Over', 'Больше').replace('Under', 'Меньше')
    def translate_team_name(name: Any) -> str: return str(name or '')

UTC = timezone.utc
EXPORT_DIR = Path('.data/exports')
OUT_JSON = EXPORT_DIR / 'latest-detailed-run-report.json'
OUT_TXT = EXPORT_DIR / 'latest-detailed-run-report.txt'
SENT_STATE = Path('.data/detailed-run-report-sent.json')
FRESHNESS_MINUTES = 45


def env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    return default if raw in (None, '') else str(raw).strip().lower() in {'1','true','yes','on','force'}


def env_int(name: str, default: int) -> int:
    try: return int(float(str(os.getenv(name, default))))
    except Exception: return default


def as_float(value: Any, default: float = 0.0) -> float:
    try: return float(str(value).replace(',', '.')) if value not in (None, '') else default
    except Exception: return default


def as_int(value: Any, default: int = 0) -> int:
    try: return int(float(str(value).replace(',', '.'))) if value not in (None, '') else default
    except Exception: return default


def load_json(path: str | Path, default: Any) -> Any:
    try:
        p = Path(path)
        if p.exists() and p.stat().st_size > 0:
            return json.loads(p.read_text(encoding='utf-8', errors='replace'))
    except Exception:
        pass
    return default


def write_json(path: str | Path, payload: Any) -> None:
    p = Path(path); p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')


def write_text(path: str | Path, text: str) -> None:
    p = Path(path); p.parent.mkdir(parents=True, exist_ok=True); p.write_text(text, encoding='utf-8')


def parse_dt(value: Any):
    if value in (None, ''): return None
    try:
        text = str(value).strip()
        if text.endswith('Z'): text = text[:-1] + '+00:00'
        dt = datetime.fromisoformat(text)
        return dt.replace(tzinfo=dt.tzinfo or UTC).astimezone(UTC)
    except Exception:
        return None


def payload_timestamp(payload: Any):
    if not isinstance(payload, dict): return None
    candidates = [payload.get(k) for k in ('created_at_utc','created_at','updated_at','reference_run_utc')]
    summary = payload.get('summary') if isinstance(payload.get('summary'), dict) else {}
    candidates += [summary.get(k) for k in ('current_time_utc','started_time_utc','current_time_local','started_time_local')]
    for value in candidates:
        dt = parse_dt(value)
        if dt: return dt
    return None


def newest_timestamp(*payloads: Any):
    vals = [payload_timestamp(p) for p in payloads]
    vals = [v for v in vals if v]
    return max(vals) if vals else datetime.now(UTC)


def is_fresh(payload: Any, reference: datetime | None, max_minutes: int = FRESHNESS_MINUTES) -> bool:
    ts = payload_timestamp(payload)
    if not ts: return False
    ref = reference or datetime.now(UTC)
    return abs((ref - ts).total_seconds()) <= max_minutes * 60


def freshness_row(name: str, payload: Any, reference: datetime | None) -> dict[str, Any]:
    ts = payload_timestamp(payload)
    age = round(((reference or datetime.now(UTC)) - ts).total_seconds()/60.0, 1) if ts else None
    return {'name': name, 'present': bool(payload), 'fresh': is_fresh(payload, reference), 'timestamp_utc': ts.isoformat() if ts else None, 'age_minutes_vs_reference': age}


def app_tz():
    try: return ZoneInfo(os.getenv('APP_TIMEZONE') or os.getenv('TZ') or 'Europe/Moscow')
    except Exception: return UTC


def fmt_time(value: Any) -> str:
    dt = parse_dt(value)
    return dt.astimezone(app_tz()).strftime('%d.%m.%Y %H:%M MSK') if dt else 'н/д'


def fallback_report(reference: datetime | None = None) -> dict[str, Any]:
    for path in ('artifacts/controlled-fallback-report.json', '.data/exports/latest-controlled-fallback-report.json'):
        payload = load_json(path, {})
        if isinstance(payload, dict) and payload:
            return payload if reference is None or is_fresh(payload, reference) else {}
    return {}


def debug_last_run() -> dict[str, Any]: return load_json('.logs/debug-last-run.json', {})
def run_summary_report() -> dict[str, Any]: return load_json('.data/exports/latest-run-summary.json', {})
def runtime_policy_report() -> dict[str, Any]: return load_json('.data/exports/latest-harizon-runtime-policy.json', {})
def day_inventory_summary() -> dict[str, Any]: return load_json('.data/exports/latest-day-inventory-summary.json', {})
def quota_report() -> dict[str, Any]: return load_json('.data/exports/latest-provider-request-budget.json', {}) or load_json('.data/exports/latest-provider-quota-governor.json', {})


def extract_evaluated(report: dict[str, Any]) -> list[dict[str, Any]]:
    for key in ('evaluated','candidates','checked_candidates','rejected_candidates'):
        rows = report.get(key)
        if isinstance(rows, list): return [r for r in rows if isinstance(r, dict)]
    return []


def unwrap_candidate(row: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    cand = row.get('candidate') if isinstance(row.get('candidate'), dict) else row
    metrics = row.get('metrics') if isinstance(row.get('metrics'), dict) else cand.get('metrics') if isinstance(cand.get('metrics'), dict) else {}
    reasons = row.get('reject_reasons') or row.get('reasons') or row.get('hard_reject_reasons') or cand.get('reject_reasons') or cand.get('reasons') or []
    if isinstance(reasons, str): reasons = [reasons]
    return cand, metrics, [str(x) for x in reasons if str(x).strip()]


def metric(c: dict[str, Any], m: dict[str, Any], *keys: str, default: float = 0.0) -> float:
    for k in keys:
        if k in m: return as_float(m.get(k), default)
        if k in c: return as_float(c.get(k), default)
    return default


def identity(c: dict[str, Any]) -> dict[str, str]:
    return {
        'home': str(c.get('home_team_ru') or '').strip() or translate_team_name(c.get('home_team') or c.get('home') or ''),
        'away': str(c.get('away_team_ru') or '').strip() or translate_team_name(c.get('away_team') or c.get('away') or ''),
        'league': str(c.get('league_name_ru') or '').strip() or translate_league_name(c.get('league_name') or c.get('league') or c.get('competition') or ''),
        'selection': translate_selection_text(c.get('selection') or c.get('market') or '', c.get('home_team'), c.get('away_team')),
    }


def reason_counter(report: dict[str, Any], evaluated: list[dict[str, Any]]) -> Counter:
    counter = Counter()
    for key in ('reject_reasons','reason_counts','rejection_reasons'):
        raw = report.get(key)
        if isinstance(raw, dict):
            for r, n in raw.items(): counter[str(r)] += as_int(n)
    if not counter:
        for row in evaluated:
            _, _, reasons = unwrap_candidate(row); counter.update(reasons)
    return counter


def near_misses(evaluated: list[dict[str, Any]], limit: int = 8) -> list[dict[str, Any]]:
    out, seen = [], set()
    for row in evaluated:
        c, m, rs = unwrap_candidate(row)
        key = (c.get('match_key'), c.get('home_team'), c.get('away_team'), c.get('selection'), c.get('point'), c.get('odds'))
        if key in seen: continue
        seen.add(key)
        ev = metric(c, m, 'canonical_ev_pct', 'ev_pct')
        edge = metric(c, m, 'canonical_edge_pp', 'edge_pp')
        q = metric(c, m, 'quality_score', 'quality')
        conf = metric(c, m, 'confidence')
        if ev > 0 or edge > 0 or any('tier_' in r for r in rs):
            out.append({'candidate': c, 'metrics': m, 'reasons': rs, 'score': (ev, edge, q, conf, -len(rs))})
    out.sort(key=lambda x: x['score'], reverse=True)
    return out[:limit]


def provider_work_lines(debug: dict[str, Any]) -> list[str]:
    diagnostics = debug.get('provider_diagnostics') if isinstance(debug.get('provider_diagnostics'), dict) else {}
    summary = diagnostics.get('summary') if isinstance(diagnostics.get('summary'), dict) else {}
    providers = summary.get('providers') if isinstance(summary.get('providers'), dict) else {}
    statuses = summary.get('provider_status') if isinstance(summary.get('provider_status'), dict) else {}
    lines=[]
    for name in ('odds_api_io','bzzoiro','sstats','api_football','football_data','thesportsdb','sportlogic','openfootball'):
        row = providers.get(name) if isinstance(providers.get(name), dict) else {}
        status = statuses.get(name) if isinstance(statuses.get(name), dict) else {}
        if not row and not status: continue
        stats = row.get('stats') if isinstance(row.get('stats'), dict) else {}
        if isinstance(stats.get('stats'), dict): stats = stats.get('stats')
        parts=[f"data {as_int(row.get('matches_with_data'))}/{as_int(row.get('items_total'))}"]
        for key,label in (('requests','req'),('max_http_requests_per_run','max'),('response_errors','err'),('stop_reason','stop_reason')):
            val = stats.get(key, status.get(key)) if isinstance(stats, dict) else status.get(key)
            if val is not None: parts.append(f'{label} {val}')
        if status.get('loaded') is False: parts.append(f"off:{status.get('reason') or 'not_loaded'}")
        lines.append(f'• {name}: ' + ', '.join(parts))
    return lines


def odds_account_lines(debug: dict[str, Any]) -> list[str]:
    diagnostics = debug.get('provider_diagnostics') if isinstance(debug.get('provider_diagnostics'), dict) else {}
    summary = diagnostics.get('summary') if isinstance(diagnostics.get('summary'), dict) else {}
    providers = summary.get('providers') if isinstance(summary.get('providers'), dict) else {}
    odds = providers.get('odds_api_io') if isinstance(providers.get('odds_api_io'), dict) else {}
    stats = odds.get('stats') if isinstance(odds.get('stats'), dict) else {}
    if isinstance(stats.get('stats'), dict): stats = stats.get('stats')
    accounts = stats.get('accounts') if isinstance(stats.get('accounts'), dict) else {}
    if not accounts: return []
    lines=['📈 odds-api.io routing']
    for name in ('account1','account2'):
        row = accounts.get(name) if isinstance(accounts.get(name), dict) else {}
        lines.append(f"• {name}: {row.get('bookmakers') or 'н/д'} | req {as_int(row.get('odds_requests'))} | offers {as_int(row.get('offers_parsed'))}")
    if stats.get('bookmakers_seen_names'): lines.append('• Букмекеры в ответах: ' + ', '.join(str(x) for x in stats.get('bookmakers_seen_names')[:8]))
    if stats.get('matches_with_2plus_books') is not None: lines.append(f"• Матчи с 2+ букмекерами: {as_int(stats.get('matches_with_2plus_books'))}; с 1 букмекером: {as_int(stats.get('matches_with_1_book'))}")
    return lines


def run_diagnostic_lines(debug: dict[str, Any]) -> list[str]:
    summary = ((debug.get('provider_diagnostics') or {}).get('summary') if isinstance(debug.get('provider_diagnostics'), dict) else {}) or {}
    labels = [('matches_with_any_offer_source','матчей с линиями из любого источника'),('matches_with_any_context_source','матчей с любым контекстом'),('matches_with_merged_context','матчей с объединенным контекстом'),('raw_candidates_with_derived_market_signal','raw с market-derived сигналом')]
    return [f'• {label}: {as_int(summary.get(key))}' for key,label in labels if summary.get(key) is not None]


def coverage_lines(inv: dict[str, Any]) -> list[str]:
    counts = inv.get('counts') if isinstance(inv.get('counts'), dict) else {}
    if not counts: return []
    total=as_int(counts.get('matches_total'))
    return ['📦 Дневной inventory', f'• Матчей всего: {total}', f"• С линиями: {as_int(counts.get('matches_with_odds'))}/{total}", f"• С контекстом: {as_int(counts.get('matches_with_context'))}/{total}", f"• Готово к модели: {as_int(counts.get('matches_ready_for_model'))}/{total}", f"• Ближайшие 6 часов: {as_int(counts.get('matches_next_6h_ready'))}/{as_int(counts.get('matches_next_6h'))} готово", f"• Следующие 12 часов: {as_int(counts.get('matches_next_12h_ready'))}/{as_int(counts.get('matches_next_12h'))} готово"]


def provider_lines() -> list[str]:
    payload = quota_report(); decisions = payload.get('decisions') if isinstance(payload, dict) else []
    out=[]
    if isinstance(decisions, list):
        for row in decisions:
            if isinstance(row, dict) and row.get('provider') in {'odds_api_io','bzzoiro','sstats','sportlogic','football_data','thesportsdb','weatherapi','openweathermap','oddspapi','futrixmetrics'}:
                out.append(f"• {row.get('provider')}: grant {as_int(row.get('grant'))}, reason {row.get('reason') or 'unknown'}")
    return out


def diagnostic_patch_lines() -> list[str]:
    lines=[]
    cb = load_json('.data/exports/latest-controlled-fallback-confirmation-bridge.json', {})
    if isinstance(cb, dict) and cb:
        lines.append(f"• confirmation bridge: {cb.get('status')} | patched inputs {as_int(cb.get('patched_hard_reject_inputs'))} | removed missing_sources {as_int(cb.get('removed_missing_sources'))}")
    qs = load_json('.data/exports/latest-quality-shadow-diagnostics.json', {})
    if isinstance(qs, dict) and qs:
        lines.append(f"• quality shadow: input {as_int(qs.get('input_candidates'))} | passed {as_int(qs.get('passed_quality'))} | rejected {as_int(qs.get('rejected_quality'))}")
    cp = load_json('.data/exports/latest-current-price-recheck-value.json', {})
    if isinstance(cp, dict) and cp:
        lines.append(f"• current price patch: {cp.get('version') or cp.get('status')} | hard_after {len(cp.get('hard_reasons_after') or [])}")
    zr = load_json('.data/exports/latest-zero-raw-candidate-recovery.json', {})
    if isinstance(zr, dict) and zr:
        lines.append(f"• zero-raw recovery: {zr.get('status')} | recovered {as_int(zr.get('recovered_candidates'))}")
    return lines


def build_payload() -> dict[str, Any]:
    debug_raw, run_raw = debug_last_run(), run_summary_report()
    reference = newest_timestamp(debug_raw, run_raw, fallback_report())
    debug = debug_raw if is_fresh(debug_raw, reference) else {}
    run = run_raw if is_fresh(run_raw, reference) else {}
    report = fallback_report(reference)
    summary = debug.get('summary') if isinstance(debug.get('summary'), dict) else run.get('summary') if isinstance(run.get('summary'), dict) else {}
    evaluated = extract_evaluated(report)
    reasons = reason_counter(report, evaluated)
    selected = report.get('selected_all') if isinstance(report.get('selected_all'), list) else ([report.get('selected')] if isinstance(report.get('selected'), dict) else [])
    return {'created_at': datetime.now(UTC).isoformat(), 'reference_run_utc': reference.isoformat(), 'source_freshness':[freshness_row('debug', debug_raw, reference), freshness_row('run_summary', run_raw, reference), freshness_row('fallback', fallback_report(), reference)], 'runtime_policy': runtime_policy_report(), 'day_inventory': day_inventory_summary(), 'published': bool(report.get('published') or report.get('telegram_sent') or report.get('selected_count')), 'status': report.get('status') or run.get('status') or 'no_pick', 'summary': summary, 'candidate_counts': {'evaluated': len(evaluated), 'rescue_checked': as_int(report.get('rescue_candidates_checked') or report.get('checked') or len(evaluated)), 'selected_count': as_int(report.get('selected_count'))}, 'reason_counts': dict(reasons.most_common(20)), 'selected': selected, 'near_misses': near_misses(evaluated, env_int('DETAILED_RUN_REPORT_TOP_NEAR_MISSES', 8)), 'diagnostic_lines': run_diagnostic_lines(debug), 'provider_work_lines': provider_work_lines(debug), 'coverage_pipeline_lines': coverage_lines(day_inventory_summary()), 'odds_account_lines': odds_account_lines(debug), 'provider_lines': provider_lines(), 'patch_lines': diagnostic_patch_lines()}


def explain(c: dict[str, Any], m: dict[str, Any], reasons: list[str]) -> list[str]:
    out=[]
    odds_sources=as_int(m.get('odds_sources_count', c.get('odds_sources_count', 0)))
    conf_sources=as_int(m.get('confirmation_sources_count', c.get('confirmation_sources_count', m.get('sources_count', c.get('sources_count', 0)))))
    if odds_sources or conf_sources:
        out.append(f'odds sources {odds_sources}, confirmation sources {conf_sources}')
        names = m.get('confirmation_sources') or c.get('confirmation_sources') or []
        if isinstance(names, list) and names: out.append('confirmation sources: ' + ', '.join(str(x) for x in names[:5]))
    for label, val, fmt in [('EV', metric(c,m,'canonical_ev_pct','ev_pct'), '{:+.1f}%'),('запас', metric(c,m,'canonical_edge_pp','edge_pp'), '{:+.1f} п.п.'),('уверенность', metric(c,m,'confidence'), '{:.1f}%'),('качество', metric(c,m,'quality_score','quality'), '{:.1f}')]:
        if val: out.append(f'{label} ' + fmt.format(val))
    books=as_int(m.get('books_count', c.get('books_count', 0))); sources=as_int(m.get('sources_count', c.get('sources_count', 0)))
    if books or sources: out.append(f'линии {books}, источники {sources}')
    if reasons: out.append('не прошло: ' + '; '.join(translate_reject_reason(r) for r in reasons[:5]))
    return out


def render(payload: dict[str, Any]) -> str:
    lines=['🧾 Подробный отчёт run — ' + ('прогноз опубликован' if payload.get('published') else 'прогнозов нет'), '']
    lines.append('🧭 Состояние артефактов')
    for row in payload.get('source_freshness') or []:
        status = 'свежий' if row.get('fresh') else ('устарел' if row.get('present') else 'нет')
        age = row.get('age_minutes_vs_reference')
        lines.append(f"• {row.get('name')}: {status} | {row.get('timestamp_utc') or 'н/д'}" + (f", Δ {age:+.1f} мин" if isinstance(age,(int,float)) else ''))
    lines.append('')
    pol = payload.get('runtime_policy') if isinstance(payload.get('runtime_policy'), dict) else {}
    if pol:
        env = pol.get('env_updates') if isinstance(pol.get('env_updates'), dict) else {}
        lines += ['🧭 Runtime policy', f"• Версия: {pol.get('policy_version') or 'н/д'}"]
        if env: lines.append(f"• Inventory bootstrap: {env.get('DAY_INVENTORY_BOOTSTRAP_PROVIDER') or 'н/д'} | provider merge: {env.get('DAY_INVENTORY_FORCE_PROVIDER_MERGE') or 'false'}")
        lines.append('')
    if payload.get('coverage_pipeline_lines'): lines += payload['coverage_pipeline_lines'] + ['']
    s=payload.get('summary') or {}; counts=payload.get('candidate_counts') or {}
    lines += ['⚙️ Что сделал скрипт', f"• Матчи: {as_int(s.get('matches_seen'))} | с линиями: {as_int(s.get('matches_with_offers'))} | контекстов: {as_int(s.get('contexts_built'))}", f"• Кандидаты: raw {as_int(s.get('candidates_raw'))} | до качества {as_int(s.get('candidates_before_quality'))} | publishable {as_int(s.get('candidates_publishable'))}", f"• Резерв проверил: {counts.get('rescue_checked',0)} | оценено в отчёте: {counts.get('evaluated',0)} | выбрано: {counts.get('selected_count',0)}"]
    lines += (payload.get('diagnostic_lines') or [])[:4] + ['']
    if payload.get('patch_lines'): lines += ['🧩 Диагностика патчей'] + payload['patch_lines'][:6] + ['']
    if payload.get('provider_work_lines'): lines += ['📡 Источники / фактическая работа'] + payload['provider_work_lines'][:8] + ['']
    if payload.get('odds_account_lines'): lines += payload['odds_account_lines'] + ['']
    reasons=Counter(payload.get('reason_counts') or {})
    if reasons:
        total=sum(reasons.values()); lines.append('🚫 Почему не прошли')
        for reason,count in reasons.most_common(10): lines.append(f'• {translate_reject_reason(reason)} — {count} ({(count/total*100.0 if total else 0):.0f}%)')
        lines.append('')
    near=payload.get('near_misses') or []
    if near:
        lines.append('⚠️ Пограничные кандидаты')
        for i,item in enumerate(near[:env_int('DETAILED_RUN_REPORT_TOP_NEAR_MISSES',8)],1):
            c=item['candidate']; m=item['metrics']; ident=identity(c); odds=metric(c,m,'odds',default=as_float(c.get('odds')))
            lines += [f"{i}. {ident['home']} — {ident['away']}", f"   🏆 {ident['league']}", f"   🎯 {ident['selection']} @{odds:.2f} | 🕒 {fmt_time(c.get('commence_time') or c.get('start_time') or c.get('kickoff'))}"]
            lines += [f'   • {x}' for x in explain(c,m,item['reasons'])]
        lines.append('')
    if payload.get('provider_lines'): lines += ['🔌 API / квоты последнего run'] + payload['provider_lines'][:10] + ['']
    top = reasons.most_common(1)[0][0] if reasons else ''
    lines += ['📌 Вывод']
    if 'confirmation' in top or 'sources' in top or 'подтверж' in top:
        lines.append('• Главный блокер — недостаток независимых confirmation/context источников для fallback-кандидатов.')
    elif 'price_integrity' in top or 'price integrity' in top:
        lines.append('• Главный блокер — price integrity: выбранная цена выглядит выбросом относительно рынка.')
    else:
        lines.append('• Прогнозов нет из-за комбинации value, качества, источников и финальных safety-guard’ов.')
    lines += ['', '🛠 Исправления по аудиту', '• В отчёт добавлены patch diagnostics: confirmation bridge, quality shadow, zero-raw recovery и current price patch.', '• API/матчинг надо оценивать по свежести, покрытию контекста и причинам отказа, а не только по числу запросов.', '', '⚖️ Дисклеймер: аналитика бота не гарантирует результат и не является финансовой рекомендацией.']
    return normalize_telegram_text('\n'.join(lines))


def split_text(text: str, limit: int = 3900) -> list[str]:
    if len(text) <= limit: return [text]
    parts=[]; cur=[]; size=0
    for line in text.splitlines():
        add=len(line)+1
        if cur and size+add>limit:
            parts.append('\n'.join(cur)); cur=[]; size=0
        cur.append(line); size+=add
    if cur: parts.append('\n'.join(cur))
    total=len(parts)
    return [f'🧾 Подробный отчёт run — часть {i}/{total}\n\n{p}' for i,p in enumerate(parts,1)] if total>1 else parts


def send_telegram(text: str) -> dict[str, Any]:
    token=os.getenv('TELEGRAM_TOKEN') or os.getenv('TELEGRAM_BOT_TOKEN'); chat_id=os.getenv('TELEGRAM_CHAT_ID')
    if not token or not chat_id: return {'sent':False,'reason':'missing_credentials'}
    h=hashlib.sha1(text.encode('utf-8')).hexdigest(); state=load_json(SENT_STATE,{})
    if isinstance(state,dict) and state.get('last_hash')==h and not env_bool('DETAILED_RUN_REPORT_FORCE_SEND',False): return {'sent':False,'reason':'unchanged','hash':h}
    url='https://api.telegram.org/bot' + str(token) + '/sendMessage'
    parts=split_text(text, env_int('TELEGRAM_MESSAGE_SOFT_LIMIT', 3900))
    try:
        for part in parts:
            data=parse.urlencode({'chat_id':chat_id,'text':part,'disable_web_page_preview':'true'}).encode('utf-8')
            req=request.Request(url,data=data,method='POST')
            with request.urlopen(req,timeout=20) as resp: resp.read()
    except Exception as exc:
        return {'sent':False,'reason':'telegram_send_error','error':repr(exc),'hash':h}
    write_json(SENT_STATE, {'last_hash':h,'sent_at':datetime.now(UTC).isoformat(),'parts':len(parts)})
    return {'sent':True,'parts':len(parts),'hash':h}


def main() -> int:
    payload=build_payload(); text=render(payload); payload['text']=text
    should_send=env_bool('DETAILED_RUN_REPORT_SEND_TELEGRAM', False)
    if payload.get('published') and not env_bool('DETAILED_RUN_REPORT_SEND_WHEN_PUBLISHED', False): should_send=False
    payload['telegram']=send_telegram(text) if should_send else {'sent':False,'reason':'disabled_or_published'}
    write_json(OUT_JSON,payload); write_text(OUT_TXT,text); print(text); return 0

if __name__ == '__main__':
    raise SystemExit(main())
