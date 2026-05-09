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

KNOWN_ARTIFACTS = [
    '.logs/debug-last-run.json',
    '.data/exports/latest-run-bot.log',
    '.data/exports/latest-controlled-fallback-report.json',
    '.data/exports/latest-provider-request-budget.json',
    '.data/exports/latest-provider-quota-governor.json',
    '.data/exports/latest-harizon-runtime-policy.json',
    '.data/exports/latest-day-inventory-priority-and-line-state.json',
    '.data/exports/latest-day-inventory-refresh-plan.json',
    '.data/exports/latest-line-movement-guard-report.json',
    '.data/exports/latest-rescue-candidates.json',
    '.data/exports/latest-candidates-before-quality.json',
    '.data/exports/latest-candidates-after-quality.json',
    '.data/exports/latest-candidates.json',
    '.data/line_history/latest.json',
]


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
        if isinstance(value, bool):
            return int(value)
        return int(float(str(value)))
    except Exception:
        return default


def as_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ''):
            return default
        return float(str(value).replace(',', '.'))
    except Exception:
        return default


def fmt_money(value: Any) -> str:
    return f'{as_float(value):.2f}'


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


def short(value: Any, limit: int = 120) -> str:
    text = str(value or '').replace('\n', ' ').strip()
    return text if len(text) <= limit else text[: max(0, limit - 1)].rstrip() + '…'


def file_status(path: str | Path) -> dict[str, Any]:
    p = Path(path)
    if not p.exists():
        return {'path': str(p), 'exists': False, 'size': 0, 'mtime': None}
    try:
        st = p.stat()
        return {
            'path': str(p),
            'exists': True,
            'size': int(st.st_size),
            'mtime': datetime.fromtimestamp(st.st_mtime, UTC).isoformat(),
        }
    except Exception:
        return {'path': str(p), 'exists': True, 'size': 0, 'mtime': None}


def freshness_minutes(path: str | Path) -> float | None:
    p = Path(path)
    try:
        if not p.exists():
            return None
        return max(0.0, (datetime.now(UTC).timestamp() - p.stat().st_mtime) / 60.0)
    except Exception:
        return None


def summary_payload() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    debug = load_json(DEBUG_PATH, {})
    fallback = load_json(EXPORT_DIR / 'latest-controlled-fallback-report.json', {})
    request_budget = load_json(EXPORT_DIR / 'latest-provider-request-budget.json', {})
    effective_policy = load_json(EXPORT_DIR / 'latest-harizon-runtime-policy.json', {})
    quota = load_json(EXPORT_DIR / 'latest-provider-quota-governor.json', {})
    refresh_plan = load_json(EXPORT_DIR / 'latest-day-inventory-refresh-plan.json', {})
    line_guard = load_json(EXPORT_DIR / 'latest-line-movement-guard-report.json', {})
    priority_state = load_json(EXPORT_DIR / 'latest-day-inventory-priority-and-line-state.json', {})
    summary = debug.get('summary') if isinstance(debug.get('summary'), dict) else {}
    return summary, debug, fallback, request_budget or effective_policy, quota, refresh_plan, line_guard, priority_state


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
    prefix = f'• {name}: ' if name else '• '
    return prefix + ', '.join(parts)


def important_source_lines(source_stats: dict[str, Any]) -> list[str]:
    order = [
        'odds_api_io', 'odds_api_io_bootstrap', 'match_bootstrap', 'allsportsapi', 'bzzoiro', 'sstats',
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
        preview = str(odds.get('last_body_preview') or '').replace('\n', ' ')[:240]
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
        'controlled_prefilter_rescue_candidates_built': 'controlled prefilter rescue построил кандидатов',
        'controlled_rescue_candidates_built': 'controlled consensus rescue построил кандидатов',
        'controlled_rescue_no_candidate': 'controlled rescue не нашёл безопасного кандидата',
        'line_guard:current_ev_below_floor': 'line guard: текущий EV ниже пола',
        'line_guard:current_edge_below_floor': 'line guard: текущий edge ниже пола',
    }
    return mapping.get(str(reason), str(reason).replace('_', ' '))


def rejection_lines(debug: dict[str, Any], fallback: dict[str, Any], line_guard: dict[str, Any]) -> list[str]:
    counters = Counter()
    for src in (debug.get('rejections'), fallback.get('reason_counts'), fallback.get('reject_reasons'), fallback.get('top_reject_reasons')):
        if isinstance(src, dict):
            for key, value in src.items():
                counters[str(key)] += as_int(value)
    if isinstance(line_guard, dict) and as_int(line_guard.get('candidates_dropped')) > 0:
        counters['line_movement_guard_dropped'] += as_int(line_guard.get('candidates_dropped'))
    if not counters:
        if isinstance(fallback.get('reason'), str):
            counters[fallback.get('reason')] += 1
        elif isinstance(fallback.get('diagnostics'), dict) and fallback['diagnostics'].get('reason'):
            counters[str(fallback['diagnostics'].get('reason'))] += 1
    if not counters:
        return ['• Нет свежей расшифровки reject reasons.']
    total = sum(counters.values()) or 1
    out = []
    for reason, count in counters.most_common(14):
        pct = round(count * 100.0 / total)
        out.append(f'• {reason_text(reason)} — {count} ({pct}%)')
    return out


def candidate_value(candidate: dict[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        if key in candidate and candidate.get(key) not in (None, ''):
            return candidate.get(key)
    metrics = candidate.get('metrics') if isinstance(candidate.get('metrics'), dict) else {}
    diagnostics = candidate.get('diagnostics') if isinstance(candidate.get('diagnostics'), dict) else {}
    source_summary = candidate.get('source_summary') if isinstance(candidate.get('source_summary'), dict) else {}
    for src in (metrics, diagnostics, source_summary):
        for key in keys:
            if key in src and src.get(key) not in (None, ''):
                return src.get(key)
    return default


def selection_label(candidate: dict[str, Any]) -> str:
    selection = str(candidate.get('selection') or candidate.get('selection_key') or candidate.get('market') or 'ставка')
    point = candidate.get('point')
    family = str(candidate.get('family') or '').lower()
    if family == 'dnb' and point in (0, 0.0, '0') and '(0)' not in selection:
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
    edge = candidate_value(candidate, 'edge_pp', 'edge_pct', 'canonical_edge_pp', default=0)
    market_prob = candidate_value(candidate, 'market_probability', 'consensus_probability', default=None)
    model_prob = candidate_value(candidate, 'adjusted_probability', 'final_probability', 'model_probability', default=None)
    books = candidate_value(candidate, 'books_count', 'lines_count', 'bookmakers_count', 'odds_books_count', default=0)
    odds_sources = candidate_value(candidate, 'odds_sources_count', 'sources_count', default=0)
    paired_books = candidate_value(candidate, 'paired_books_count', default=None)
    xh = candidate_value(candidate, 'expected_home', 'xg_home', default=None)
    xa = candidate_value(candidate, 'expected_away', 'xg_away', default=None)
    line_guard = candidate.get('line_movement_guard') if isinstance(candidate.get('line_movement_guard'), dict) else {}
    source_summary = candidate.get('source_summary') if isinstance(candidate.get('source_summary'), dict) else {}
    confirmation_sources = candidate.get('confirmation_sources') or candidate.get('context_sources') or source_summary.get('confirmation_sources') or []
    if isinstance(confirmation_sources, str):
        confirmation_sources = [x.strip() for x in re.split(r'[,;/|]+', confirmation_sources) if x.strip()]
    if not isinstance(confirmation_sources, list):
        confirmation_sources = []
    tier = str(candidate.get('tier') or candidate.get('level') or 'A')
    kickoff = candidate.get('commence_time') or candidate.get('kickoff') or candidate.get('start_time') or 'н/д'
    status = candidate.get('pre_kickoff_status') or line_guard.get('final_pre_kickoff_check')
    header = [
        f'{idx}. {home} — {away}',
        f'   🏆 {league}',
        f'   🎯 {selection_label(candidate)} @{odds:.2f} | 🕒 {kickoff}',
        f'   • odds sources {as_int(odds_sources)}, books/lines {as_int(books)}, confirmation sources {len(confirmation_sources)}',
        f'   • confirmation sources: {", ".join(map(str, confirmation_sources[:8])) or "н/д"}',
        f'   • уровень {tier} | EV {as_float(ev):+.1f}% | запас {as_float(edge):+.1f} п.п. | уверенность {as_float(confidence):.1f}% | качество {as_float(quality):.1f}',
    ]
    if market_prob is not None or model_prob is not None:
        header.append(f'   • рынок {as_float(market_prob) * 100:.1f}% | модель {as_float(model_prob) * 100:.1f}%')
    if paired_books is not None:
        header.append(f'   • paired books: {as_int(paired_books)} | bookmaker: {candidate.get("bookmaker") or source_summary.get("selected_bookmaker") or "н/д"}')
    if xh is not None or xa is not None:
        header.append(f'   • xG: {as_float(xh):.2f} : {as_float(xa):.2f}')
    if isinstance(line_guard, dict) and line_guard:
        passed = 'ok' if line_guard.get('passed') else 'blocked'
        move = line_guard.get('line_move_pct')
        lead = line_guard.get('lead_minutes')
        header.append(f'   • line guard: {passed} | движение {as_float(move):+.1f}% | до матча {as_float(lead):.0f} мин')
    if status:
        header.append(f'   • pre-kickoff статус: {status}')
    return header


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


def artifact_candidates(limit: int = 8) -> list[dict[str, Any]]:
    paths = [
        EXPORT_DIR / 'latest-rescue-candidates.json',
        EXPORT_DIR / 'latest-candidates-before-quality.json',
        EXPORT_DIR / 'latest-candidates-after-quality.json',
        EXPORT_DIR / 'latest-candidates.json',
    ]
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for path in paths:
        payload = load_json(path, None)
        candidates: list[dict[str, Any]] = []
        if isinstance(payload, list):
            candidates = [x for x in payload if isinstance(x, dict)]
        elif isinstance(payload, dict):
            for key in ('candidates', 'rows', 'data', 'top_candidates', 'selected_candidates'):
                value = payload.get(key)
                if isinstance(value, list):
                    candidates = [x for x in value if isinstance(x, dict)]
                    break
        for row in candidates:
            key = '|'.join(str(row.get(k) or '') for k in ('match_key', 'family', 'selection_key', 'selection', 'point'))
            if key in seen:
                continue
            seen.add(key)
            row = dict(row)
            row['_artifact_path'] = str(path)
            rows.append(row)
            if len(rows) >= limit:
                return rows
    return rows


def api_breakdown_lines(source_stats: dict[str, Any], request_budget: dict[str, Any], quota: dict[str, Any]) -> list[str]:
    decisions = request_budget.get('decisions') if isinstance(request_budget.get('decisions'), list) else []
    by_provider = {str(row.get('provider')): row for row in decisions if isinstance(row, dict)}
    descriptions = {
        'odds_api_io': 'главный источник линий и списка матчей по целевым букмекерам',
        'allsportsapi': 'fixture/secondary odds probe; расширяет inventory и проверяет доп. линии',
        'bzzoiro': 'контекст/прогнозы; независимое подтверждение',
        'sstats': 'форма/статистика команд; основной near-window context provider',
        'football_data': 'fixture/league calendar; добор матчей и алиасы',
        'thesportsdb': 'fixture/алиасы команд и лиг',
        'weather': 'погодный overlay: WeatherAPI first, OpenWeatherMap fallback',
        'sportlogic': 'probe/context; secondary odds rescue только под контролем',
        'espn': 'вспомогательный публичный контекст и алиасы',
        'openligadb': 'вспомогательный календарь/контекст',
        'futrixmetrics': 'доп. context provider; держим отключённым при low yield',
    }
    out: list[str] = []
    for name in descriptions:
        row = source_stats.get(name) if isinstance(source_stats.get(name), dict) else {}
        decision = by_provider.get(name, {})
        if not row and not decision:
            continue
        out.append(f'• {name}: {descriptions[name]}')
        out.append('  - runtime: ' + provider_line('', row).replace('• ', '') if row else '  - runtime: не участвовал в прогнозном runtime')
        if decision:
            out.append(f"  - квота: grant {as_int(decision.get('grant'))}, reason {decision.get('reason') or 'granted'}, status {decision.get('status') or 'n/a'}")
    return out


def model_debug_lines(debug: dict[str, Any], fallback: dict[str, Any]) -> list[str]:
    model_debug = debug.get('model_debug') if isinstance(debug.get('model_debug'), dict) else {}
    rows = model_debug.get('matches') if isinstance(model_debug.get('matches'), list) else []
    candidates_debug = model_debug.get('candidates') if isinstance(model_debug.get('candidates'), list) else []
    rejections = debug.get('rejections') if isinstance(debug.get('rejections'), dict) else {}
    out = ['🧪 Candidate factory / модель']
    out.append(f'• model_debug rows: matches {len(rows)} | candidates {len(candidates_debug)}')
    if rejections:
        top = ', '.join(f'{reason_text(k)}={as_int(v)}' for k, v in Counter({str(k): as_int(v) for k, v in rejections.items()}).most_common(8))
        out.append(f'• model rejections top: {top}')
    controlled = model_debug.get('controlled_consensus_rescue') if isinstance(model_debug.get('controlled_consensus_rescue'), dict) else {}
    if controlled:
        out.append(f"• controlled rescue: enabled {controlled.get('enabled')} | built {as_int(controlled.get('built'))} | returned {as_int(controlled.get('returned'))}")
    if rows:
        out.append('• pre-filter sample:')
        for row in rows[:5]:
            if not isinstance(row, dict):
                continue
            out.append(f"  - {short(row.get('match_key'), 38)} | {row.get('family') or row.get('market')} | {row.get('selection') or row.get('selection_key')} | conf {as_float(row.get('confidence')):.1f} | score {as_float(row.get('publication_score')):.1f}")
    return out


def refresh_plan_lines(refresh_plan: dict[str, Any], priority_state: dict[str, Any]) -> list[str]:
    out = ['⏱️ Накопление inventory и ближайшие матчи']
    if not isinstance(refresh_plan, dict) or not refresh_plan:
        return out + ['• refresh-plan artifact не найден.']
    out.append(f"• Активных матчей: {as_int(refresh_plan.get('active_matches'))} | требуют odds refresh: {as_int(refresh_plan.get('matches_needing_odds_refresh'))}")
    out.append(f"• Final pre-kickoff checks: {as_int(refresh_plan.get('final_pre_kickoff_checks'))} | без ещё одного обычного run до старта: {as_int(refresh_plan.get('no_more_regular_run_before_kickoff'))}")
    state_refresh = priority_state.get('refresh_plan') if isinstance(priority_state.get('refresh_plan'), dict) else {}
    if state_refresh:
        out.append(f"• Priority state updated: {priority_state.get('updated_at_utc') or state_refresh.get('updated_at_utc') or 'н/д'}")
    top = refresh_plan.get('top_priority_matches') if isinstance(refresh_plan.get('top_priority_matches'), list) else []
    if top:
        out.append('• Top priority upcoming:')
        for row in top[:6]:
            if not isinstance(row, dict):
                continue
            home = row.get('home_team') or '?'
            away = row.get('away_team') or '?'
            status = row.get('pre_kickoff_status') or 'н/д'
            minutes = row.get('minutes_to_kickoff')
            need = (row.get('refresh_plan') or {}).get('needs_odds_refresh') if isinstance(row.get('refresh_plan'), dict) else None
            final = (row.get('refresh_plan') or {}).get('final_pre_kickoff_check_required') if isinstance(row.get('refresh_plan'), dict) else None
            out.append(f"  - {home} — {away} | до старта {as_float(minutes):.0f} мин | {status} | odds_refresh={need} | final_check={final}")
    return out


def line_guard_lines(line_guard: dict[str, Any]) -> list[str]:
    out = ['🛡️ Line movement / pre-publish guard']
    if not isinstance(line_guard, dict) or not line_guard:
        return out + ['• line guard artifact не найден.']
    out.append(f"• candidates seen {as_int(line_guard.get('candidates_seen'))} | kept {as_int(line_guard.get('candidates_kept'))} | dropped {as_int(line_guard.get('candidates_dropped'))}")
    out.append(f"• drop_bad_candidates={line_guard.get('drop_bad_candidates')} | updated {line_guard.get('updated_at_utc') or 'н/д'}")
    files = line_guard.get('files') if isinstance(line_guard.get('files'), list) else []
    for file_row in files[:4]:
        if not isinstance(file_row, dict):
            continue
        out.append(f"• {Path(str(file_row.get('path') or '')).name}: seen {as_int(file_row.get('seen'))}, kept {as_int(file_row.get('kept'))}, dropped {as_int(file_row.get('dropped'))}")
        sample = file_row.get('dropped_sample') if isinstance(file_row.get('dropped_sample'), list) else []
        for dropped in sample[:2]:
            if isinstance(dropped, dict):
                guard = dropped.get('guard') if isinstance(dropped.get('guard'), dict) else {}
                reasons = guard.get('reasons') if isinstance(guard.get('reasons'), list) else []
                out.append(f"  - DROP {short(dropped.get('selection'), 42)} @{as_float(dropped.get('odds')):.2f}: {'; '.join(map(str, reasons[:3])) or 'н/д'}")
    return out


def artifact_lines() -> list[str]:
    out = ['📂 Артефакты run']
    for path in KNOWN_ARTIFACTS:
        status = file_status(path)
        if status['exists']:
            fresh = freshness_minutes(path)
            fresh_text = f', Δ {fresh:.1f} мин' if fresh is not None else ''
            out.append(f"• {Path(path).name}: есть | {status['size']} bytes{fresh_text}")
        else:
            out.append(f"• {Path(path).name}: нет")
    return out


def quick_status_lines(summary: dict[str, Any], fallback: dict[str, Any], source_stats: dict[str, Any], refresh_plan: dict[str, Any], line_guard: dict[str, Any]) -> list[str]:
    matches_seen = as_int(summary.get('matches_seen'))
    matches_with_offers = as_int(summary.get('matches_with_offers'))
    contexts = as_int(summary.get('contexts_built'))
    raw = as_int(summary.get('candidates_raw'))
    before_q = as_int(summary.get('candidates_before_quality'))
    published = as_int(summary.get('published')) or len(published_candidates(fallback))
    odds = source_stats.get('odds_api_io') if isinstance(source_stats.get('odds_api_io'), dict) else {}
    offers = as_int(odds.get('offers_parsed'))
    final_checks = as_int(refresh_plan.get('final_pre_kickoff_checks')) if isinstance(refresh_plan, dict) else 0
    dropped = as_int(line_guard.get('candidates_dropped')) if isinstance(line_guard, dict) else 0
    status = '✅ прогноз опубликован' if published else '🟡 прогнозов нет'
    if matches_with_offers <= 0:
        status = '🔴 нет свежих линий'
    elif raw <= 0 and before_q <= 0:
        status = '🟠 линии есть, raw-кандидатов нет'
    elif raw > 0 and published <= 0:
        status = '🟡 кандидаты есть, guards не выпустили'
    return [
        '🚦 Быстрый статус',
        f'• Итог: {status}',
        f'• Покрытие: матчи {matches_seen}, линии {matches_with_offers}, контекст {contexts}, offers {offers}',
        f'• Воронка: raw {raw}, до качества {before_q}, опубликовано {published}',
        f'• Pre-kickoff: final checks {final_checks}, line-guard dropped {dropped}',
    ]


def diagnosis_lines(summary: dict[str, Any], fallback: dict[str, Any], source_stats: dict[str, Any], refresh_plan: dict[str, Any], line_guard: dict[str, Any]) -> list[str]:
    matches_with_offers = as_int(summary.get('matches_with_offers'))
    contexts = as_int(summary.get('contexts_built'))
    raw = as_int(summary.get('candidates_raw'))
    before_q = as_int(summary.get('candidates_before_quality'))
    publishable = as_int(summary.get('candidates_publishable'))
    published = as_int(summary.get('published')) or len(published_candidates(fallback))
    odds = source_stats.get('odds_api_io') if isinstance(source_stats.get('odds_api_io'), dict) else {}
    offers = as_int(odds.get('offers_parsed'))
    out = ['📌 Вывод']
    if published > 0:
        out.append('• Контролируемый прогноз выбран и должен уходить отдельным Telegram-сообщением.')
        out.append('• В отчёте смотри опубликованный pick, пограничные кандидаты, line guard и reasons — это полный след принятия решения.')
    elif matches_with_offers <= 0 or offers <= 0:
        out.append('• Главный стопор — нет свежих odds-offers. Прогноз без актуальной линии запрещён.')
        out.append('• Приоритет: odds-api.io parser/endpoint, затем controlled secondary odds rescue.')
    elif raw <= 0 and before_q <= 0:
        out.append('• Линии есть, но raw pool пустой. Приоритет: candidate factory / controlled prefilter rescue / market-family mapping.')
        if contexts <= 0:
            out.append('• Контекст тоже пустой: нужно проверить SStats/Bzzoiro/ClubElo matching.')
    elif publishable <= 0:
        out.append('• Кандидаты были, но quality/publication guards не выпустили ставку. Это защита качества, а не ошибка Telegram.')
        out.append('• Смотри блоки “Почему не прошли”, “Line movement guard” и “Пограничные кандидаты”.')
    else:
        out.append('• Run прошёл, но публикации нет: проверь duplicate guard, Telegram publish flag и controlled fallback report.')
    final_checks = as_int(refresh_plan.get('final_pre_kickoff_checks')) if isinstance(refresh_plan, dict) else 0
    if final_checks > 0:
        out.append(f'• Есть {final_checks} final pre-kickoff checks: эти матчи нужно проверять особенно внимательно, потому что следующий cron-run может не успеть.')
    dropped = as_int(line_guard.get('candidates_dropped')) if isinstance(line_guard, dict) else 0
    if dropped > 0:
        out.append(f'• Line guard снял {dropped} кандидатов: value/edge/цена ушли до публикации.')
    out.append('• Guards не должны ослабляться: лучше 0 прогнозов, чем ставка без свежей линии и подтверждения.')
    return out


def build_report() -> str:
    summary, debug, fallback, request_budget, quota, refresh_plan, line_guard, priority_state = summary_payload()
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
    lines.extend(quick_status_lines(summary, fallback, source_stats, refresh_plan, line_guard))
    lines.append('')
    lines.append('🧭 Runtime policy')
    lines.append(f'• Версия: {policy_version}')
    lines.append(f'• Время запуска: {started}')
    lines.append(f"• Inventory bootstrap: {os.getenv('DAY_INVENTORY_BOOTSTRAP_PROVIDER') or 'odds_api_io'} | provider merge: {os.getenv('DAY_INVENTORY_FORCE_PROVIDER_MERGE') or 'false'}")
    lines.append(f"• Окно публикации: {os.getenv('PUBLISH_WINDOW_HOURS') or '12'} ч | min lead: {os.getenv('MIN_KICKOFF_LEAD_MINUTES') or '30'} мин | cron interval: {os.getenv('CRON_EXPECTED_INTERVAL_MINUTES') or '120'} мин")
    lines.append(f'• Банк: {fmt_money(bank)} | открытый риск: {fmt_money(open_risk)} | доступно: {fmt_money(bank - open_risk)}')
    lines.append('')
    lines.append('📦 Дневной inventory')
    lines.append(f'• Матчей всего: {matches_total}')
    lines.append(f'• С линиями: {matches_with_offers}/{matches_total}')
    lines.append(f'• С контекстом: {contexts}/{matches_total}')
    lines.append(f'• Готово к модели: {min(matches_with_offers, contexts)}/{matches_total}')
    lines.append(f'• Ближайшие 6 часов: {near6_ready}/{matches_seen} готово')
    lines.append(f'• Следующие 12 часов: {min(matches_with_offers, contexts)}/{matches_seen} готово')
    lines.append('')
    lines.extend(refresh_plan_lines(refresh_plan, priority_state))
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
    lines.extend(model_debug_lines(debug, fallback))
    lines.append('')
    lines.extend(line_guard_lines(line_guard))
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
    lines.extend(rejection_lines(debug, fallback, line_guard))
    if picks:
        lines.append('')
        lines.append('✅ Опубликовано')
        for i, candidate in enumerate(picks, 1):
            lines.extend(candidate_lines(candidate, i, compact_mode=True))
            stake = candidate_value(candidate, 'stake', 'stake_amount', default=None)
            if stake is not None:
                lines.append(f'   • ставка {fmt_money(stake)}')
    borders = border_candidates(fallback, 5)
    if borders:
        lines.append('')
        lines.append('⚠️ Остальные пограничные кандидаты')
        for i, candidate in enumerate(borders, 1):
            lines.extend(candidate_lines(candidate, i, compact_mode=True))
            reasons = candidate.get('reject_reasons') or candidate.get('reasons') or candidate.get('block_reasons') or []
            if isinstance(reasons, list) and reasons:
                lines.append(f"   • не прошло: {'; '.join(map(str, reasons[:3]))}")
    raw_artifact_candidates = artifact_candidates(5)
    if raw_artifact_candidates and not borders:
        lines.append('')
        lines.append('⚠️ Кандидаты из свежих артефактов')
        for i, candidate in enumerate(raw_artifact_candidates, 1):
            lines.extend(candidate_lines(candidate, i, compact_mode=True))
            lines.append(f"   • artifact: {Path(str(candidate.get('_artifact_path') or '')).name}")
    lines.append('')
    lines.append('🧠 Автообучение')
    closed = as_int((debug.get('auto_learning') or {}).get('closed_sample') if isinstance(debug.get('auto_learning'), dict) else 0)
    required = as_int((debug.get('auto_learning') or {}).get('required_sample') if isinstance(debug.get('auto_learning'), dict) else 30, 30)
    lines.append(f'• Выборка: {closed}/{required} закрытых ставок | sample_ready={str(closed >= required).lower()}')
    lines.append('• Режим: observe_only')
    lines.append('• Фильтры не менялись: идёт накопление статистики.')
    lines.append('')
    lines.append('🧩 Работа API — разбор')
    lines.extend(api_breakdown_lines(source_stats, request_budget, quota) or ['• Нет подробного API breakdown в свежих артефактах.'])
    lines.append('')
    lines.append('🔌 API / квоты последнего run')
    decisions = request_budget.get('decisions') if isinstance(request_budget.get('decisions'), list) else []
    if decisions:
        for row in decisions[:30]:
            if isinstance(row, dict):
                skip = f", skip {row.get('skip_reason')}" if row.get('skip_reason') else ''
                lines.append(f"• {row.get('provider')}: grant {as_int(row.get('grant'))}, reason {row.get('reason') or 'granted'}{skip}")
    else:
        lines.append('• нет свежих provider budget decisions')
    lines.append('')
    lines.extend(artifact_lines())
    lines.append('')
    lines.extend(diagnosis_lines(summary, fallback, source_stats, refresh_plan, line_guard))
    lines.append('')
    lines.append('⚖️ Дисклеймер: это аналитический отчёт бота, не гарантия результата и не финансовая рекомендация.')
    return '\n'.join(lines).strip() + '\n'


def split_messages(text: str, limit: int) -> list[str]:
    # Telegram sendMessage accepts 1-4096 chars. Keep a soft limit below that to
    # leave room for the part header and avoid TEXT_TOO_LONG.
    limit = max(1200, min(limit, 3900))
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
    write_json(OUT_JSON, {'messages': len(chunks), 'sent': sent, 'path': str(OUT_TXT), 'style': 'harizon_reference_v2_full_observability'})
    print(json.dumps({'messages': len(chunks), 'sent': sent, 'style': 'harizon_reference_v2_full_observability'}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
