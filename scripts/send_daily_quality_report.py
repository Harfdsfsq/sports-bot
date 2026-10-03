"""Human-readable Telegram report from current-run artifacts only."""
from __future__ import annotations

import os
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from app.services.daily_match_registry import parse_time, read_json, write_json
from scripts.send_pipeline_run_report import send_telegram

REASONS = {
    'unsupported_spread_line': 'четвертная или некорректная фора не поддерживается',
    'post_calibration_probability_guard': 'вероятность ниже финального порога модели',
    'quality_post_calibration_probability_guard': 'вероятность ниже финального порога модели',
    'quality_high_odds_confidence_guard': 'недостаточная уверенность при повышенном коэффициенте',
    'no_bet_quality_score_guard': 'качество ниже минимальных 65 баллов',
    'quality_no_bet_quality_score_guard': 'качество ниже минимальных 65 баллов',
    'unsupported_total_line': 'тотал вне поддерживаемых целых и половинных линий',
    'unsupported_team_total_line': 'индивидуальный тотал вне поддерживаемых линий',
    'non_core_confidence_guard': 'недостаточная уверенность для менее изученного турнира',
    'confidence_below_threshold': 'вероятность ниже порога модели',
    'publish_books_guard': 'недостаточное подтверждение цены',
    'quality_quality_high_odds_books_guard': 'недостаточное подтверждение цены',
    'quality_quality_high_odds_confidence_guard': 'недостаточная уверенность при повышенном коэффициенте',
    'market_outside_daily_policy': 'рынок не входит в правила дневного отбора',
    'missing_real_line_or_context': 'нет реальной линии или спортивного контекста',
    'below_b_quality': 'качество или ценность ниже порога B',
    'missing_goal_model': 'нет данных для голевой модели',
    'line_or_value_changed': 'линия изменилась или потеряла ценность',
    'bad_historical_segment_guard': 'неблагоприятные результаты похожих прогнозов',
    'quality_bad_historical_segment_guard': 'неблагоприятные результаты похожих прогнозов',
    'daily_price_observed_at_stale': 'коэффициент устарел',
    'daily_context_observed_at_stale': 'контекст устарел',
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
    lines = ['🧾 HARIZON — отчёт по запуску',
             ('🧪 Проверка без отправки прогнозов' if dry else f'✅ Отправлено прогнозов: {picks}' if picks else '🟡 Подходящих прогнозов не отправлено'),
             '', '📦 Дневной инвентарь — ' + str(summary.get('current_time_local', '')[:10]), f"Собрано {c.get('inventory', 0)}/300 матчей. Каждый матч имеет постоянную привязку к командам и времени начала.",
             f"Данные получены за день: линия {c.get('collected_line', 0)}, контекст {c.get('collected_context', 0)}.",
             f"Актуально сейчас: линия {c.get('line', 0)}, контекст {c.get('context', 0)}, оба вида данных {c.get('ready', 0)}.",
             f"Инвентарь следующего дня: {daily.get('next_day_inventory', 0)}/300 матчей подготовлено.",
             f"Матчей после полуночи в очереди ближайшего окна: {c.get('lookahead', 0)}. Они считаются отдельно от сегодняшних 300.",
             '', '⏱️ Ближайшее окно — 4 часа', f"Матчей с запасом 30+ минут: {c.get('near', 0)}. С актуальной линией и контекстом: {c.get('near_ready', 0)}.",
             f"В этом окне: линия {c.get('near_line', 0)}, спортивный контекст {c.get('near_context', 0)}; нет линии у {c.get('near_missing_line', 0)}, нет контекста у {c.get('near_missing_context', 0)}.",
             f"Дополнительные данные окна: форма {c.get('form', 0)}, таблица {c.get('standings', 0)}, погода {c.get('weather', 0)}.",
             '', '🏷️ Качество прогнозов',
             'A: качество 78+, уверенность модели 70+, EV 5%+, запас 3 п.п.+.',
             'B: качество 65+, уверенность модели 60+, EV 3%+, запас 2 п.п.+.',
             'Для обоих: реальный контекст, текущая линия и положительная ценность. Два API — преимущество.',
             f'За день отправлено: A {a}, B {b}; всего {len(published)}/5.',
             'Цель отбора: 1+ A и 2+ B за день; при недостаточном качестве ставок будет меньше.',
             '', '🧪 Воронка',
             f"Кандидатов до качества: {summary.get('candidates_before_quality', 0)}; после качества: {summary.get('candidates_raw', 0)}; выбрано: {len(daily.get('selected') or [])}."]
    if daily.get('selected'):
        lines.append('Подборка текущего запуска:')
        for row in daily['selected']:
            lines.append(f"• {row['match']} | {row['tier']}-tier | @{row['odds']:.2f} | качество {row.get('quality') or 0:.1f}")
            if row.get('selection'):
                kickoff = parse_time(row.get('kickoff_utc'))
                from app.services.daily_match_registry import TZ
                when = kickoff.astimezone(TZ).strftime('%d.%m %H:%M MSK') if kickoff else 'время не указано'
                lines.append(f"  {row['selection']} · {when} · модель {row.get('probability_pct', 0):.1f}% · EV {row.get('ev_pct', 0):+.1f}% · сумма {row.get('stake_amount', 0):.2f}")
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
        for reason, value in reasons.items():
            label = REASONS.get(reason)
            if label is None:
                label = 'нет контекста для рынка' if 'missing_context' in reason else 'ценность ниже порога' if 'edge' in reason or 'ev_' in reason else 'дополнительная проверка модели или цены'
            groups[label] += value
        for label, value in groups.most_common(5):
            lines.append(f'• {label}: {value}')
        lines.append('Счётчики относятся к вариантам ставок; один матч может иметь несколько вариантов.')
    if summary.get('telegram_delivery_errors'):
        lines.append('⚠️ Часть прогнозов не доставлена в Telegram; учитываются только подтверждённые отправки.')
    if any(isinstance(stats, dict) and stats.get('response_errors') for stats in (summary.get('source_stats') or {}).values()):
        lines.append('⚠️ Часть данных недоступна у источников; подробности сохранены в артефакте.')
    if c.get('inventory', 0) < 300:
        lines.append('ℹ️ Сегодняшний инвентарь ниже 300. При первом запуске поздно вечером он содержит оставшиеся доступные матчи; полный следующий день показан отдельно.')
    if c.get('near_ready', 0) < c.get('near', 0):
        lines.append('⚠️ Ближайшее окно покрыто частично; отсутствующие данные остаются в очереди.')
    lines.extend(['', 'Проценты модели — оценки, а не подтверждённая проходимость. A/B обозначает качество отбора, а не гарантированный исход.',
                  f"Run {os.getenv('GITHUB_RUN_ID', 'local')} · {os.getenv('GITHUB_REF_NAME', 'local')}"])
    return '\n'.join(lines)


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
    chunks = [text[i:i + 3500] for i in range(0, len(text), 3500)]
    sent = False if dry else all(send_telegram(chunk) for chunk in chunks)
    write_json('.data/exports/latest-daily-quality-report.json', {'created_at_utc': datetime.now(UTC).isoformat(), 'summary_valid': valid, 'telegram_sent': sent, 'dry_run': dry, 'text': text})
    Path('.data/exports/latest-daily-quality-report.txt').write_text(text + '\n')
    print(text)
    return 0 if valid and (dry or sent) else 1


if __name__ == '__main__':
    raise SystemExit(main())
