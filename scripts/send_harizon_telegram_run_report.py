from __future__ import annotations

import json
import os
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib import parse, request

UTC = timezone.utc
EXPORT_DIR = Path('.data/exports')
DEBUG_PATH = Path('.logs/debug-last-run.json')
OUT_TXT = EXPORT_DIR / 'latest-harizon-telegram-run-report.txt'
OUT_JSON = EXPORT_DIR / 'latest-harizon-telegram-run-report.json'


def load_json(path: str | Path, default: Any) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except Exception:
        return default


def write_text(path: str | Path, text: str) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding='utf-8')


def write_json(path: str | Path, payload: Any) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')


def env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == '':
        return default
    return str(raw).strip().lower() in {'1', 'true', 'yes', 'on', 'force'}


def env_int(name: str, default: int) -> int:
    try:
        raw = os.getenv(name)
        return int(float(str(raw))) if raw not in (None, '') else default
    except Exception:
        return default


def as_int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ''):
            return default
        return int(float(str(value)))
    except Exception:
        return default


def as_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ''):
            return default
        return float(str(value))
    except Exception:
        return default


def fmt_pct(value: Any, signed: bool = False) -> str:
    number = as_float(value)
    return f'{number:+.1f}%' if signed else f'{number:.1f}%'


def fmt_pp(value: Any, signed: bool = True) -> str:
    number = as_float(value)
    return f'{number:+.1f} п.п.' if signed else f'{number:.1f} п.п.'


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


def compact(value: Any) -> str:
    if isinstance(value, float):
        return f'{value:.3f}'.rstrip('0').rstrip('.')
    return str(value)


def summary_payload() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    debug = load_json(DEBUG_PATH, {})
    fallback = load_json(EXPORT_DIR / 'latest-controlled-fallback-report.json', {})
    request_budget = load_json(EXPORT_DIR / 'latest-provider-request-budget.json', {})
    effective_policy = load_json(EXPORT_DIR / 'latest-harizon-runtime-policy.json', {})
    quota = load_json(EXPORT_DIR / 'latest-provider-quota-governor.json', {})
    summary = debug.get('summary') if isinstance(debug.get('summary'), dict) else {}
    return summary, debug, fallback, request_budget or effective_policy, quota


def nested(row: dict[str, Any], key: str, default: Any = None) -> Any:
    for src in (row, row.get('stats'), row.get('status')):
        if isinstance(src, dict) and key in src:
            return src.get(key)
    return default


def source_stats_from(summary: dict[str, Any], debug: dict[str, Any]) -> dict[str, Any]:
    for candidate in (summary.get('source_stats'), debug.get('source_stats')):
        if isinstance(candidate, dict):
            return candidate
    return {}


def provider_line(name: str, row: dict[str, Any]) -> str:
    parts: list[str] = []
    if 'matches_with_data' in row or 'items_total' in row:
        parts.append(f"data {as_int(row.get('matches_with_data'))}/{as_int(row.get('items_total'))}")
    mapping = [
        ('event_requests', 'events req'),
        ('odds_requests', 'odds req'),
        ('requests', 'req'),
        ('response_errors', 'err'),
        ('events_fetched', 'events'),
        ('matches_built', 'matches'),
        ('events_matched', 'matched'),
        ('offers_parsed', 'offers'),
        ('contexts_built', 'ctx'),
        ('rows_fetched', 'rows'),
        ('games_fetched', 'games_fetched'),
        ('fixtures_fetched', 'fixtures'),
        ('weatherapi_requests', 'weatherapi'),
        ('openweathermap_requests', 'owm'),
        ('cache_hits', 'cache'),
        ('budget_exhausted', 'budget_exhausted'),
    ]
    for key, label in mapping:
        value = nested(row, key)
        if value in (None, '', [], {}):
            continue
        if isinstance(value, bool):
            if value:
                parts.append(label)
        else:
            parts.append(f'{label} {compact(value)}')
    if not parts:
        enabled = nested(row, 'enabled')
        parts.append('disabled' if enabled is False else 'нет данных')
    return f'• {name}: ' + ', '.join(parts)


def important_source_lines(source_stats: dict[str, Any]) -> list[str]:
    order = [
        'odds_api_io', 'odds_api_io_bootstrap', 'allsportsapi', 'bzzoiro', 'sstats',
        'football_data', 'thesportsdb', 'sportlogic', 'espn', 'openligadb', 'openfootball',
        'futrixmetrics', 'weather', 'newsapi', 'currents', 'gnews', 'self_history', 'market_monitor',
    ]
    rows: list[str] = []
    for name in order:
        row = source_stats.get(name)
        if isinstance(row, dict):
            rows.append(provider_line(name, row))
    for name, row in source_stats.items():
        if name not in order and isinstance(row, dict):
            rows.append(provider_line(str(name), row))
    return rows


def odds_routing_lines(source_stats: dict[str, Any]) -> list[str]:
    odds = source_stats.get('odds_api_io') if isinstance(source_stats.get('odds_api_io'), dict) else {}
    if not odds:
        return []
    lines = ['📈 odds-api.io routing']
    accounts = odds.get('accounts') if isinstance(odds.get('accounts'), dict) else {}
    if accounts:
        for name in ('account1', 'account2'):
            row = accounts.get(name)
            if not isinstance(row, dict):
                continue
            books = row.get('bookmakers') or 'н/д'
            req = as_int(row.get('odds_requests'))
            offers = as_int(row.get('offers_parsed'))
            err = as_int(row.get('response_errors'))
            suffix = f', err {err}' if err else ''
            lines.append(f'• {name}: {books} | req {req} | offers {offers}{suffix}')
    books = odds.get('bookmakers_seen_names') or []
    if isinstance(books, list) and books:
        lines.append('• Букмекеры в ответах: ' + ', '.join(str(x) for x in books[:10]))
    lines.append(f"• Матчи с 2+ букмекерами: {as_int(odds.get('matches_with_2plus_books'))}; с 1 букмекером: {as_int(odds.get('matches_with_1_book'))}")
    if odds.get('last_body_preview') and as_int(odds.get('offers_parsed')) == 0:
        preview = str(odds.get('last_body_preview') or '').replace('\n', ' ')[:220]
        lines.append(f'• Последний ответ odds: {preview}')
    return lines


def reason_text(reason: str) -> str:
    mapping = {
        'fallback_publish_no_candidate': 'fallback-публикация: нет кандидата',
        'duplicate_prediction': 'такой прогноз уже отправлялся ранее',
        'edge_below_threshold': 'запас value ниже минимума',
        'ev_below_threshold': 'EV ниже минимума',
        'confidence_below_threshold': 'уверенность ниже минимума',
        'family_not_allowed': 'закрытая семья рынка',
        'market_integrity_spreads_quarantined': 'форы закрыты до проверки handicap-parser',
        'market_integrity_insufficient_market_depth': 'малая глубина рынка',
    }
    return mapping.get(str(reason), str(reason).replace('_', ' '))


def rejection_lines(debug: dict[str, Any], fallback: dict[str, Any]) -> list[str]:
    counters = Counter()
    for src in (debug.get('rejections'), fallback.get('reason_counts'), fallback.get('reject_reasons'), fallback.get('top_reject_reasons')):
        if isinstance(src, dict):
            for key, value in src.items():
                counters[str(key)] += as_int(value)
    if not counters:
        if isinstance(fallback.get('reason'), str):
            counters[fallback.get('reason')] += 1
        elif isinstance(fallback.get('diagnostics'), dict) and fallback['diagnostics'].get('reason'):
            counters[str(fallback['diagnostics'].get('reason'))] += 1
    if not counters:
        return ['• Нет свежей расшифровки reject reasons.']
    total = sum(counters.values()) or 1
    out = []
    for reason, count in counters.most_common(10):
        pct = round(count * 100.0 / total)
        out.append(f'• {reason_text(reason)} — {count} ({pct}%)')
    return out


def candidate_value(candidate: dict[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        if key in candidate and candidate.get(key) not in (None, ''):
            return candidate.get(key)
    metrics = candidate.get('metrics') if isinstance(candidate.get('metrics'), dict) else {}
    for key in keys:
        if key in metrics and metrics.get(key) not in (None, ''):
            return metrics.get(key)
    return default


def selection_label(candidate: dict[str, Any]) -> str:
    selection = str(candidate.get('selection') or candidate.get('selection_key') or candidate.get('market') or 'ставка')
    point = candidate.get('point')
    family = str(candidate.get('family') or '').lower()
    if family == 'dnb' and point in (0, 0.0, '0'):
        return f'{selection} (0)'
    if point not in (None, '') and str(point) not in selection:
        return f'{selection} ({point})'
    return selection


def candidate_lines(candidate: dict[str, Any], idx: int, compact_mode: bool = False) -> list[str]:
    home = str(candidate.get('home_team') or candidate.get('home') or '?')
    away = str(candidate.get('away_team') or candidate.get('away') or '?')
    league = str(candidate.get('league_name') or candidate.get('league') or 'н/д')
    odds = as_float(candidate.get('odds') or candidate.get('price') or candidate.get('selected_odds'))
    confidence = candidate_value(candidate, 'confidence', 'confidence_pct', default=0)
    quality = candidate_value(candidate, 'quality_score', 'quality', 'publication_score', default=0)
    ev = candidate_value(candidate, 'ev_pct', 'canonical_ev_pct', default=0)
    edge = candidate_value(candidate, 'edge_pp', 'canonical_edge_pp', default=0)
    books = candidate_value(candidate, 'books_count', 'lines_count', 'bookmakers_count', default=0)
    odds_sources = candidate_value(candidate, 'odds_sources_count', 'sources_count', default=0)
    confirmation_sources = candidate.get('confirmation_sources') or candidate.get('context_sources') or []
    if isinstance(confirmation_sources, str):
        confirmation_sources = [x.strip() for x in re.split(r'[,;/|]+', confirmation_sources) if x.strip()]
    if not isinstance(confirmation_sources, list):
        confirmation_sources = []
    tier = str(candidate.get('tier') or candidate.get('level') or 'A')
    kickoff = candidate.get('commence_time') or candidate.get('kickoff') or candidate.get('start_time') or 'н/д'
    if compact_mode:
        return [
            f'{idx}. {home} — {away}',
            f'   🏆 {league}',
            f'   🎯 {selection_label(candidate)} @{odds:.2f} | 🕒 {kickoff}',
            f'   • odds sources {as_int(odds_sources)}, confirmation sources {len(confirmation_sources)}',
            f'   • confirmation sources: {", ".join(map(str, confirmation_sources[:6])) or "н/д"}',
            f'   • уровень {tier}',
            f'   • EV {as_float(ev):+.1f}% | запас {as_float(edge):+.1f} п.п.',
            f'   • уверенность {as_float(confidence):.1f}% | качество {as_float(quality):.1f}',
            f'   • линии {as_int(books)}, источники {max(as_int(odds_sources), len(confirmation_sources))}',
        ]
    return [
        f'{idx}. {home} — {away}',
        f'🎯 Ставка: {selection_label(candidate)}',
        f'💸 Коэффициент: {odds:.2f}',
        f'✅ Уверенность: {as_float(confidence):.1f}% | качество {as_float(quality):.1f} | уровень {tier}',
        f'📚 Линии: {as_int(books)} | odds sources: {as_int(odds_sources)} | confirmation sources: {len(confirmation_sources)}',
        f'🔎 Подтверждения: {", ".join(map(str, confirmation_sources[:6])) or "н/д"}',
        f'🧮 Контрольная ценность: запас {as_float(edge):+.1f} п.п. | EV {as_float(ev):+.1f}%',
        f'🏆 Турнир: {league}',
        f'🕒 Начало: {kickoff}',
    ]


def published_candidates(fallback: dict[str, Any]) -> list[dict[str, Any]]:
    for key in ('published_candidates', 'selected_candidates', 'selected', 'chosen', 'picks'):
        value = fallback.get(key)
        if isinstance(value, list):
            return [x for x in value if isinstance(x, dict)]
        if isinstance(value, dict):
            return [value]
    return []


def border_candidates(fallback: dict[str, Any], limit: int = 5) -> list[dict[str, Any]]:
    for key in ('borderline_candidates', 'evaluated_candidates', 'top_candidates', 'watchlist', 'rejected_candidates'):
        value = fallback.get(key)
        if isinstance(value, list):
            return [x for x in value if isinstance(x, dict)][:limit]
    return []


def api_breakdown_lines(source_stats: dict[str, Any], request_budget: dict[str, Any], quota: dict[str, Any]) -> list[str]:
    decisions = request_budget.get('decisions') if isinstance(request_budget.get('decisions'), list) else []
    by_provider = {str(row.get('provider')): row for row in decisions if isinstance(row, dict)}
    descriptions = {
        'odds_api_io': 'главный источник линий и списка матчей по целевым букмекерам',
        'allsportsapi': 'fixture/secondary odds probe; помогает расширять inventory и проверять доп. линии',
        'bzzoiro': 'контекст/прогнозы; независимое подтверждение',
        'sstats': 'форма/статистика команд; основной near-window context provider',
        'football_data': 'fixture/league calendar; добор матчей и алиасы',
        'thesportsdb': 'fixture/алиасы команд и лиг',
        'weather': 'погодный overlay: WeatherAPI first, OpenWeatherMap fallback',
        'sportlogic': 'probe/context; odds остаются под контролем до подтверждения схемы',
        'espn': 'вспомогательный публичный контекст и алиасы',
        'openligadb': 'вспомогательный календарь/контекст',
    }
    out: list[str] = []
    for name in descriptions:
        row = source_stats.get(name) if isinstance(source_stats.get(name), dict) else {}
        decision = by_provider.get(name, {})
        if not row and not decision:
            continue
        out.append(f'• {name}: {descriptions[name]}')
        out.append('  - runtime: ' + provider_line('', row).replace('• : ', '') if row else '  - runtime: не участвовал в прогнозном runtime')
        if decision:
            out.append(f"  - квота: grant {as_int(decision.get('grant'))}, reason {decision.get('reason') or 'granted'}, status {decision.get('status') or 'n/a'}")
    return out


def build_report() -> str:
    summary, debug, fallback, request_budget, quota = summary_payload()
    source_stats = source_stats_from(summary, debug)
    picks = published_candidates(fallback)
    published = as_int(summary.get('published')) or len(picks)
    title = '🧾 Подробный отчёт run — прогноз опубликован' if published > 0 else '🧾 Подробный отчёт run — прогнозов нет'
    policy_version = os.getenv('HARIZON_EFFECTIVE_RUNTIME_POLICY_VERSION') or request_budget.get('runtime_policy_version') or request_budget.get('version') or 'unknown'
    started = summary.get('started_time_local') or summary.get('current_time_local') or datetime.now(UTC).isoformat()
    bank = as_float(summary.get('bankroll_current_balance') or summary.get('bank') or 1000.0, 1000.0)
    open_risk = as_float(summary.get('bankroll_open_exposure') or summary.get('open_risk') or 0.0)
    matches_total = as_int(summary.get('matches_before_publish_window') or summary.get('matches_seen'))
    matches_seen = as_int(summary.get('matches_seen'))
    matches_with_offers = as_int(summary.get('matches_with_offers'))
    contexts = as_int(summary.get('contexts_built'))
    candidates_raw = as_int(summary.get('candidates_raw'))
    candidates_before_quality = as_int(summary.get('candidates_before_quality'))
    publishable = as_int(summary.get('candidates_publishable'))
    fallback_checked = as_int(fallback.get('checked') or fallback.get('evaluated') or fallback.get('candidates_checked') or candidates_before_quality)
    fallback_selected = len(picks)
    mapping = summary.get('mapping') if isinstance(summary.get('mapping'), dict) else {}
    filtering = summary.get('filtering') if isinstance(summary.get('filtering'), dict) else {}
    near6_ready = min(matches_with_offers, matches_seen)
    lines: list[str] = []
    lines.append(title)
    lines.append('')
    lines.append('🧭 Runtime policy')
    lines.append(f'• Версия: {policy_version}')
    lines.append(f"• Inventory bootstrap: {os.getenv('DAY_INVENTORY_BOOTSTRAP_PROVIDER') or 'odds_api_io'} | provider merge: {os.getenv('DAY_INVENTORY_FORCE_PROVIDER_MERGE') or 'false'}")
    lines.append('')
    lines.append('📦 Дневной inventory')
    lines.append(f'• Матчей всего: {matches_total}')
    lines.append(f'• С линиями: {matches_with_offers}/{matches_total}')
    lines.append(f'• С контекстом: {contexts}/{matches_total}')
    lines.append(f'• Готово к модели: {min(matches_with_offers, contexts)}/{matches_total}')
    lines.append(f'• Ближайшие 6 часов: {near6_ready}/{matches_seen} готово')
    lines.append(f'• Следующие 12 часов: {min(matches_with_offers, contexts)}/{matches_seen} готово')
    lines.append('')
    lines.append('⚙️ Что сделал скрипт')
    lines.append(f'• Матчи: {matches_seen} | с линиями: {matches_with_offers} | контекстов: {contexts}')
    lines.append(f'• Кандидаты: raw {candidates_raw} | до качества {candidates_before_quality} | publishable {publishable}')
    lines.append(f'• Резерв проверил: {fallback_checked} | оценено в отчёте: {fallback_checked} | выбрано: {fallback_selected}')
    lines.append(f'• матчей с линиями из любого источника: {matches_with_offers}')
    lines.append(f'• матчей с любым контекстом: {contexts}')
    lines.append(f'• матчей с объединенным контекстом: {contexts}')
    lines.append(f"• raw с market-derived сигналом: {as_int(summary.get('market_derived_candidates') or 0)}")
    if filtering:
        lines.append(f"• Фильтр времени: after {as_int(filtering.get('total_after'))}/{as_int(filtering.get('total_before'))}, outside {as_int(filtering.get('skipped_outside_window'))}, too soon {as_int(filtering.get('skipped_too_soon'))}, started {as_int(filtering.get('skipped_started'))}")
    if mapping:
        lines.append(f"• Matching: odds exact {as_int(mapping.get('matched_exact'))}, loose {as_int(mapping.get('matched_loose'))}, fuzzy {as_int(mapping.get('matched_fuzzy'))}; sstats exact {as_int(mapping.get('sstats_exact'))}, bzzoiro ctx {as_int(mapping.get('bzzoiro_contexts'))}, thesportsdb ctx {as_int(mapping.get('thesportsdb_contexts'))}")
    lines.append('')
    lines.append('📡 Источники / фактическая работа')
    src_lines = important_source_lines(source_stats)
    lines.extend(src_lines or ['• нет свежих source_stats'])
    odds_lines = odds_routing_lines(source_stats)
    if odds_lines:
        lines.append('')
        lines.extend(odds_lines)
    lines.append('')
    lines.append('🚫 Почему не прошли')
    lines.extend(rejection_lines(debug, fallback))
    if picks:
        lines.append('')
        lines.append('✅ Опубликовано')
        for i, candidate in enumerate(picks, 1):
            lines.extend(candidate_lines(candidate, i, compact_mode=True))
    borders = border_candidates(fallback, 5)
    if borders:
        lines.append('')
        lines.append('⚠️ Остальные пограничные кандидаты')
        for i, candidate in enumerate(borders, 1):
            lines.extend(candidate_lines(candidate, i, compact_mode=True))
            reasons = candidate.get('reject_reasons') or candidate.get('reasons') or candidate.get('block_reasons') or []
            if isinstance(reasons, list) and reasons:
                lines.append(f"   • не прошло: {'; '.join(map(str, reasons[:3]))}")
    lines.append('')
    lines.append('🧠 Автообучение')
    lines.append('• Выборка: 0/30 закрытых ставок | sample_ready=false')
    lines.append('• Режим: observe_only')
    lines.append('• Фильтры не менялись: идёт накопление статистики.')
    lines.append('')
    lines.append('🧩 Работа API — разбор')
    lines.extend(api_breakdown_lines(source_stats, request_budget, quota) or ['• Нет подробного API breakdown в свежих артефактах.'])
    lines.append('')
    lines.append('🔌 API / квоты последнего run')
    for row in (request_budget.get('decisions') if isinstance(request_budget.get('decisions'), list) else [])[:25]:
        if isinstance(row, dict):
            lines.append(f"• {row.get('provider')}: grant {as_int(row.get('grant'))}, reason {row.get('reason') or 'granted'}")
    lines.append('')
    lines.append('📌 Вывод')
    if published > 0:
        lines.append('• Контролируемый прогноз выбран; он должен уходить отдельным Telegram-сообщением при live-publish режиме.')
        lines.append('• Guards не ослаблялись: публикация разрешается только для нового кандидата с достаточным подтверждением и стабильной value.')
    elif matches_with_offers <= 0:
        lines.append('• Главный стопор этого run — нет свежих odds-offers. Модель не должна строить прогнозы без актуальной линии.')
        lines.append('• Следующий приоритет — восстановить offers_parsed у odds-api.io или включить проверенный резервный odds-source.')
    elif candidates_before_quality <= 0:
        lines.append('• Линии есть, но кандидаты не построились: следующий приоритет — market-family parser и candidate factory.')
    else:
        lines.append('• Кандидаты были, но quality/publication guards не выпустили ставку. Это защита качества, не ошибка Telegram.')
    lines.append('')
    lines.append('⚖️ Дисклеймер: это аналитический отчёт бота, не гарантия результата и не финансовая рекомендация.')
    return '\n'.join(lines).strip() + '\n'


def split_messages(text: str, limit: int) -> list[str]:
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    current: list[str] = []
    size = 0
    for line in text.splitlines():
        add = len(line) + 1
        if current and size + add > limit:
            chunks.append('\n'.join(current).strip())
            current = []
            size = 0
        current.append(line)
        size += add
    if current:
        chunks.append('\n'.join(current).strip())
    total = len(chunks)
    return [f'🧾 Подробный отчёт run — часть {i}/{total}\n\n{chunk}' if total > 1 else chunk for i, chunk in enumerate(chunks, 1)]


def send_telegram(text: str) -> bool:
    token = os.getenv('TELEGRAM_BOT_TOKEN') or os.getenv('TELEGRAM_TOKEN')
    chat_id = os.getenv('TELEGRAM_CHAT_ID')
    if not token or not chat_id:
        return False
    data = parse.urlencode({'chat_id': chat_id, 'text': text, 'disable_web_page_preview': 'true'}).encode()
    url = f'https://api.telegram.org/bot{token}/sendMessage'
    try:
        with request.urlopen(request.Request(url, data=data), timeout=20) as resp:
            return 200 <= int(resp.status) < 300
    except Exception:
        return False


def main() -> int:
    report = build_report()
    write_text(OUT_TXT, report)
    limit = env_int('TELEGRAM_MESSAGE_SOFT_LIMIT', 3600)
    chunks = split_messages(report, limit)
    sent = 0
    if env_bool('ENHANCED_RUN_REPORT_SEND_TELEGRAM', True):
        for chunk in chunks:
            if send_telegram(chunk):
                sent += 1
    write_json(OUT_JSON, {'messages': len(chunks), 'sent': sent, 'path': str(OUT_TXT), 'style': 'harizon_reference_v1'})
    print(json.dumps({'messages': len(chunks), 'sent': sent, 'style': 'harizon_reference_v1'}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
