"""Human-readable Telegram report from current-run artifacts only."""
from __future__ import annotations

import os
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from app.services.daily_match_registry import parse_time, read_json, write_json
from app.services.daily_quality_policy import TIER_LIMITS
from scripts.send_pipeline_run_report import send_telegram

REASONS = {
    'reserve_only': 'рынок проверяется только в резерве без публикации',
    'insufficient_confirmed_sample': 'подтверждённая выборка команд меньше минимальной',
    'context_history_identity_missing': 'история команды не содержит проверяемых названий участников',
    'context_payload_invalid': 'источник вернул спортивные данные в неподдерживаемом формате',
    'fixture_squad_ambiguous': 'источник не уточнил состав клуба в турнире с резервными командами',
    'fixture_squad_conflict': 'указанный состав команды противоречит участникам турнира',
    'insufficient_team_sample': 'слишком мало подтверждённых матчей обеих команд для прогноза',
    'quality_insufficient_team_sample': 'слишком мало подтверждённых матчей обеих команд для прогноза',
    'context_team_identity_mismatch': 'спортивные данные относятся к другой команде или составу',
    'context_fixture_identity_mismatch': 'спортивные данные относятся к другому матчу',
    'context_source_stale': 'спортивный источник устарел',
    'context_source_expired_before_publication': 'спортивные данные устарели до публикации',
    'partial_sporting_context': 'нет подтверждённых данных обеих команд',
    'unsupported_market_period': 'линия относится к тайму или другому периоду, а не ко всему матчу',
    'selected_price_outlier': 'выбранный коэффициент резко отличается от цен других букмекеров',
    'daily_publication_limit': 'достигнут дневной лимит публикаций',
    'unsupported_spread_line': 'четвертная или некорректная фора не поддерживается',
    'post_calibration_probability_guard': 'вероятность ниже финального порога модели',
    'quality_post_calibration_probability_guard': 'вероятность ниже финального порога модели',
    'quality_high_odds_confidence_guard': 'недостаточная уверенность при повышенном коэффициенте',
    'no_bet_quality_score_guard': 'качество ниже минимальных 65 баллов',
    'quality_no_bet_quality_score_guard': 'качество ниже минимальных 65 баллов',
    'unsupported_total_line': 'тотал вне поддерживаемых целых и половинных линий',
    'unsupported_team_total_line': 'индивидуальный тотал вне поддерживаемых линий',
    'non_core_confidence_guard': 'недостаточная уверенность для менее изученного турнира',
    'non_core_context_guard': 'спортивные данные недостаточно подтверждают модель',
    'totals_over25_confidence_guard': 'для ТБ(2.5) недостаточна уверенность модели',
    'totals_over25_edge_guard': 'для ТБ(2.5) недостаточен запас над линией',
    'totals_over25_probability_guard': 'для ТБ(2.5) недостаточна скорректированная вероятность',
    'totals_over25_xg_guard': 'ТБ(2.5) не подтверждён голевой моделью',
    'totals_over25_dual_threat_guard': 'ТБ(2.5) слишком зависит от голов одной команды',
    'quality_quality_high_odds_score_guard': 'качество недостаточно при повышенном коэффициенте',
    'confidence_below_threshold': 'недостаточная уверенность модели',
    'probability_below_threshold': 'вероятность исхода ниже порога модели',
    'publish_books_guard': 'недостаточное подтверждение цены',
    'quality_quality_high_odds_books_guard': 'недостаточное подтверждение цены',
    'quality_quality_high_odds_confidence_guard': 'недостаточная уверенность при повышенном коэффициенте',
    'market_outside_daily_policy': 'рынок не входит в правила дневного отбора',
    'missing_real_line_or_context': 'нет реальной линии или спортивного контекста',
    'below_b_quality': 'данные, уверенность или ценность ниже требований B-tier',
    'missing_goal_model': 'нет данных для голевой модели',
    'line_or_value_changed': 'линия изменилась или потеряла ценность',
    'bad_historical_segment_guard': 'неблагоприятные результаты похожих прогнозов',
    'quality_bad_historical_segment_guard': 'неблагоприятные результаты похожих прогнозов',
    'daily_price_observed_at_stale': 'коэффициент устарел',
    'daily_context_observed_at_stale': 'контекст устарел',
    'already_published_match': 'на этот матч рекомендация уже отправлена',
    'final_kickoff_window': 'до начала осталось меньше 30 минут',
    'kickoff_outside_30m_4h': 'матч вне окна от 30 минут до 4 часов',
}


def render(summary):
    daily = summary.get('daily_quality') or {}
    c = daily.get('coverage') or {}
    published = daily.get('published_today') or []
    a, b = sum(row['tier'] == 'A' for row in published), sum(row['tier'] == 'B' for row in published)
    picks = int(summary.get('published_to_telegram') or 0)
    dry = bool(summary.get('dry_run'))
    duplicates = int(summary.get('skipped_already_in_state') or 0)
    only_duplicates = (int(summary.get('candidates_raw') or 0) > 0
                       and duplicates >= int(summary.get('candidates_raw') or 0))
    lines = ['🧾 HARIZON — отчёт по запуску',
             ('🧪 Проверка без отправки прогнозов' if dry else f'✅ Отправлено прогнозов: {picks}' if picks else '⏸ Новые прогнозы не отправлены: дневной лимит достигнут' if len(published) >= 5 else 'ℹ️ Прошедшие отбор ставки уже отправлены; повторная отправка пропущена' if only_duplicates else '🟡 Подходящих прогнозов не отправлено'),
             '', '📦 Дневной инвентарь — ' + str(summary.get('current_time_local', '')[:10]), f"Собрано {c.get('inventory', 0)} матчей при цели 300. Каждый матч имеет постоянную привязку к командам и времени начала.",
             f"Данные получены за день: линия {c.get('collected_line', 0)}, контекст {c.get('collected_context', 0)}.",
             f"Актуально сейчас: линия {c.get('line', 0)}, контекст {c.get('context', 0)}, оба вида данных {c.get('ready', 0)}.",
             f"Инвентарь следующего дня: {str(daily.get('next_day_inventory', 0)) + '/300 матчей подготовлено' if daily.get('next_day_inventory') else 'будет подготовлен вечером; сейчас приоритет у текущего дня'}.",
             f"Матчей после полуночи в очереди ближайшего окна: {c.get('lookahead', 0)}. Они считаются отдельно от сегодняшних 300.",
             '', '⏱️ Ближайшее окно — 4 часа', f"Матчей с запасом 30+ минут: {c.get('near', 0)}. С актуальной линией и контекстом: {c.get('near_ready', 0)}.",
             f"В этом окне: линия {c.get('near_line', 0)}, спортивный контекст {c.get('near_context', 0)}; нет линии у {c.get('near_missing_line', 0)}, нет контекста у {c.get('near_missing_context', 0)}.",
             f"Дополнительные данные окна: форма {c.get('form', 0)}, таблица {c.get('standings', 0)}, погода {c.get('weather', 0)}.",
             '', '🏷️ Качество прогнозов',
             *[f"{tier}: качество {limits['quality']}+, уверенность модели {limits['confidence']}+, EV {limits['ev']}%+, запас {limits['edge']} п.п.+." for tier, limits in TIER_LIMITS.items()],
             'Для обоих: реальный контекст, текущая линия и положительная ценность. Два API — преимущество.',
             'Качество оценивает данные отдельно от EV; высокий EV не повышает доверие к данным.',
             'A дополнительно требует подтверждённой выборки команд и согласованности голевых оценок. Неизвестная выборка — максимум B.',
             f'За день отправлено: A {a}, B {b}; всего {len(published)}. Дневной лимит: 5.',
             'Цель отбора: 1+ A и 2+ B за день; при недостаточном качестве ставок будет меньше.',
             '', '🧪 Воронка',
             f"Рассчитано вариантов ставок: {summary.get('candidates_model_evaluated', 'нет счётчика')}. После первичной проверки вероятности и ценности: {summary.get('candidates_before_quality', 0)}.",
             f"Кандидатов до качества: {summary.get('candidates_before_quality', 0)}; после качества: {summary.get('candidates_raw', 0)}; выбрано: {len(daily.get('selected') or [])}."]
    if 'near_missing_both' in c:
        position = next(i for i, line in enumerate(lines) if line.startswith('Дополнительные данные окна:'))
        lines.insert(position, f"Добор окна: только линия — {c.get('near_only_line', 0)}; только контекст — {c.get('near_only_context', 0)}; нет обоих — {c['near_missing_both']}.")
    if duplicates:
        lines.append(f'Защита от повторов: ранее опубликованных вариантов — {duplicates}. Повторно не отправлены.')
    reserve = summary.get('reserve_markets')
    if reserve is not None:
        lines.insert(2, '📌 Публикация: только общие тоталы матча. Индивидуальные тоталы и остальные рынки — только резерв.')
    bank = summary.get('bankroll') or {}
    if bank:
        lines.insert(2, f"💼 Банк: {bank.get('current_balance', 0):.2f} | открытый риск: {bank.get('open_exposure', 0):.2f} | доступно: {bank.get('available_balance', 0):.2f}")
    if len(published) > 5:
        lines.extend(['', 'ℹ️ После восстановления истории выявлено прежнее превышение дневного лимита. Новые публикации остановлены до следующего дня MSK.'])
    if c.get('inventory', 0) > 300:
        lines.append(f"ℹ️ В реестре сохранены {c['inventory'] - 300} дополнительных матчей после объединения истории; цель 300 достигнута.")
    if daily.get('selected'):
        lines.extend(['', '📨 Опубликованные прогнозы текущего запуска:'])
        for row in daily['selected']:
            lines.append(f"• {row['match']} | {row['tier']}-tier | @{row['odds']:.2f} | качество {row.get('quality') or 0:.1f}")
            if row.get('selection'):
                kickoff = parse_time(row.get('kickoff_utc'))
                from app.services.daily_match_registry import TZ
                when = kickoff.astimezone(TZ).strftime('%d.%m %H:%M MSK') if kickoff else 'время не указано'
                lines.append(f"  {row['selection']} · {when} · модель {row.get('probability_pct', 0):.1f}% · EV {row.get('ev_pct', 0):+.1f}% · сумма {row.get('stake_amount', 0):.2f}")
    if reserve is not None:
        lines.extend(['', '🧪 Резерв — проверка без ставок и публикаций',
                      f"Рассчитано вариантов: {reserve.get('evaluated', 0)}; первичный отбор прошли: {reserve.get('passed_model', 0)}.",
                      f"Новых вариантов: {reserve.get('selected_new', 0)}; ожидают результата: {reserve.get('pending', 0)}; завершено: {reserve.get('settled', 0)}.",
                      f"Выигрыши {reserve.get('wins', 0)}, проигрыши {reserve.get('losses', 0)}, возвраты {reserve.get('pushes', 0)}. Условный результат при 1 единице на вариант: {reserve.get('paper_pnl', 0):+.2f}. В банк не входит."])
        reserve_reasons = Counter(reserve.get('rejections') or {})
        if reserve_reasons:
            labels = [f"{REASONS.get(reason, reason)} — {count}" for reason, count in reserve_reasons.most_common(3)]
            lines.append('Не допущены в резерв: ' + '; '.join(labels) + '.')
    reviewed = [row for row in daily.get('quality_review', []) if row.get('reasons')]
    if reviewed:
        lines.extend(['', '🔎 Почему финальные кандидаты не прошли'])
        for row in reviewed:
            labels = [REASONS.get(reason, 'дополнительная проверка модели') for reason in row['reasons']]
            point = '' if row.get('point') is None else f" ({row['point']:+g})"
            lines.append(f"• {row['match']} | {row.get('selection')}{point} @{row.get('odds') or 0:.2f}: {'; '.join(labels)}; качество {row.get('quality') or 0:.1f}.")
    reasons = Counter(daily.get('rejections') or {})
    for key, value in (summary.get('rejections') or {}).items():
        if isinstance(value, int):
            reasons[key] += value
    if reasons:
        lines.extend(['', '🚫 Основные причины отказов'])
        groups = Counter()
        policy_skips = 0
        for reason, value in reasons.items():
            # Prefetched next-window matches are deliberately outside this model run.
            if reason in {'match_not_found', 'simple_market_h2h_high_odds_skip'}:
                continue
            if reason in {'market_outside_daily_policy', 'unsupported_total_line', 'unsupported_spread_line', 'unsupported_team_total_line'}:
                policy_skips += value
                continue
            label = REASONS.get(reason)
            if label is None:
                label = 'нет контекста для рынка' if 'missing_context' in reason else 'ценность ниже порога' if 'edge' in reason or 'ev_' in reason else 'дополнительная проверка модели или цены'
            groups[label] += value
        for label, value in groups.most_common(5):
            lines.append(f'• {label}: {value}')
        if policy_skips:
            lines.append(f'Отдельно пропущены неподдерживаемые рынки и линии: {policy_skips}.')
        lines.append('Счётчики относятся к вариантам ставок; один матч может иметь несколько вариантов.')
    excluded = (summary.get('quality') or {}).get('summary', {}).get('excluded_data_incidents', 0)
    if excluded:
        lines.append(f'⚠️ Из оценки модели и обучения исключены прогнозы с подтверждёнными ошибками данных: {excluded}. Их финансовая история сохранена.')
    odds_stats = (summary.get('source_stats') or {}).get('odds_api_io') or {}
    configured = odds_stats.get('configured_accounts')
    if configured is not None:
        lines.extend(['', '📡 Источники линии'])
        for name, label in [('account1', 'Аккаунт 1'), ('account2', 'Аккаунт 2')]:
            row = (odds_stats.get('accounts') or {}).get(name) or {}
            status = ('ключ не подключён' if name not in configured else
                      'ограничение квоты; повтор после сброса' if row.get('rate_limited') or row.get('quota_exhausted') else
                      f"выбранные букмекеры недоступны по тарифу ({row.get('effective_bookmakers') or row.get('bookmakers')}); проверьте выбор букмекеров в аккаунте" if row.get('plan_restriction') else
                      'ошибка авторизации — проверьте ключ' if row.get('auth_error') else
                      f"обновил линии для {row['events_matched']} матчей в этом запуске" if row.get('events_matched') else
                      'использован свежий кеш' if not odds_stats.get('assigned_matches') else
                      'проверен; подходящих линий не получено')
            lines.append(f'Odds-api.io · {label}: {status}.')
        if 'cached_matches' in odds_stats:
            lines.append(f"С учётом свежего кеша Odds-api.io: линии для {odds_stats['cached_matches']} матчей. Это общее покрытие двух аккаунтов без повторного подсчёта матчей.")
    if any(row.get('blocked') or row.get('last_block') for row in summary.get('api_budget') or []):
        lines.append('ℹ️ Часть запросов отложена по лимитам API; полученные данные сохранены, пропуски остаются в очереди.')
    overdue = summary.get('overdue_published_bets') or []
    result_fetch = (summary.get('settlement') or {}).get('sstats_fetch') or {}
    if result_fetch and not result_fetch.get('complete'):
        lines.append('⚠️ Результаты проверены частично; неподтверждённые ставки остаются открытыми до следующей проверки.')
    if overdue:
        amount = sum(float(row.get('stake_amount') or 0) for row in overdue)
        lines.append(f'⚠️ Ожидают результата старые опубликованные ставки: {len(overdue)}, сумма {amount:.2f}. Этот риск остаётся в банке до подтверждённого расчёта.')
    if summary.get('telegram_delivery_errors'):
        lines.append('⚠️ Часть прогнозов не доставлена в Telegram; учитываются только подтверждённые отправки.')
    if any(isinstance(stats, dict) and stats.get('response_errors') for stats in (summary.get('source_stats') or {}).values()):
        lines.append('⚠️ Часть данных недоступна у источников; подробности сохранены в артефакте.')
    if c.get('inventory', 0) < 300:
        lines.append('ℹ️ Сегодняшний инвентарь ниже 300: подключённые источники пока дали меньше матчей. Это фактическое покрытие; недостающие матчи не создаются искусственно.')
    if c.get('near_ready', 0) < c.get('near', 0):
        lines.append('⚠️ Ближайшее окно покрыто частично; отсутствующие данные остаются в очереди.')
    if summary.get('goal_model_version'):
        lines.extend(['', f"🧠 Голевая модель: {summary['goal_model_version']} — без повторных надбавок за форму и таблицу."])
        version = summary['goal_model_version']
        history = (summary.get('goal_model_history') or {}).get(version, {})
        lines.append(f"История этой версии: завершено {history.get('settled', 0)}; без возврата {history.get('count', 0)}. Для калибровки нужно {os.getenv('DAILY_CALIBRATION_MIN_VERSION_BETS', '50')} ставок без возврата этой версии.")
        lines.append(f"Известная выборка для публикации: от {os.getenv('DAILY_B_MIN_TEAM_SAMPLE', '6')} матчей на команду. Одинаковые сезонные данные нескольких API не увеличивают их общий вес.")
    monitor = summary.get('market_monitor') or {}
    if 'clv_measured_total' in monitor:
        lines.append(f"Проверка цены перед матчем: измерено {monitor['clv_measured_total']}; без подходящей свежей котировки в этом запуске — {monitor.get('clv_missing_quote_now', 0)}.")
    lines.extend(['', 'Проценты модели — оценки, а не подтверждённая проходимость. A/B обозначает качество отбора, а не гарантированный исход.',
                  f"Run {os.getenv('GITHUB_RUN_ID', 'local')} · {os.getenv('GITHUB_REF_NAME', 'local')}"])
    return '\n'.join(lines)


def split_report(text, limit=3500):
    """Keep paragraph/word boundaries and Telegram's UTF-16 length budget."""
    if limit < 2:
        raise ValueError('Report chunk limit must be at least two UTF-16 units')
    chunks = []
    while text:
        if len(text.encode('utf-16-le')) // 2 <= limit:
            chunks.append(text)
            break
        prefix = text.encode('utf-16-le')[:limit * 2].decode('utf-16-le', errors='ignore')
        boundary = prefix.rfind('\n')
        if boundary <= 0:
            boundary = prefix.rfind(' ')
        chunk = prefix[:boundary + 1] if boundary > 0 else prefix
        chunks.append(chunk)
        text = text[len(chunk):]
    return chunks


def main():
    started = parse_time(os.getenv('RUN_STARTED_AT_UTC'))
    summary = read_json('.data/exports/latest-run-summary.json', {})
    observed = parse_time(summary.get('started_time_utc'))
    if started is None or observed is None or observed < started or not summary.get('daily_quality'):
        text = '🧾 HARIZON: запуск не завершил текущую сводку. Проверьте артефакт и шаг pipeline. Старые результаты не использованы.'
        valid = False
    else:
        text, valid = render(summary), True
    dry = os.getenv('PUBLISH_DRY_RUN', 'true').lower() == 'true'
    chunks = split_report(text)
    sent = False if dry else all(send_telegram(chunk) for chunk in chunks)
    write_json('.data/exports/latest-daily-quality-report.json', {'created_at_utc': datetime.now(UTC).isoformat(), 'summary_valid': valid, 'telegram_sent': sent, 'dry_run': dry, 'text': text})
    Path('.data/exports/latest-daily-quality-report.txt').write_text(text + '\n')
    print(text)
    return 0 if valid and (dry or sent) else 1


if __name__ == '__main__':
    raise SystemExit(main())
