# Полный аудит: проблемы и выводы

## Архитектурные проблемы
- Несколько workflow и режимов запуска создавали шум и усложняли сравнение прогонов.
- Ранее часть env-профилей ломала GitHub Actions из-за комментариев в `GITHUB_ENV`.
- До введения fixed-run отсутствовал единый стабильный пакет артефактов для анализа.

## Pipeline кандидатов
Проблемы:
- candidate object формируется из нескольких источников вероятностей
- часть полей переиспользуется как alias, но фактически живёт своей жизнью
- в writeup может появляться более одной model probability для одной и той же ставки
- лучшие odds и consensus/fair odds могут относиться к разным срезам данных

## Критические integrity-сбои
Нужно считать ошибкой:
- `abs((1 / odds) - implied_probability) > 0.02`
- `abs(adjusted_probability - source_summary.adjusted_probability) > 0.02`
- `edge_pct < 0` при `ev_pct > 0`
- `selected_price != odds` без явного объяснения
- `fair_odds != 1 / market_probability` больше допустимого eps

## Quality и guards
По логам системно срабатывали:
- `publish_books_guard`
- `unsupported_total_line`
- `market_derived_signal_guard_totals`
- `historical_guard`
- `no_bet_quality_score_guard`

Важно: ослаблять эти фильтры до исправления integrity нельзя.

## Стратегические проблемы
- Исторический ROI отрицательный
- Средний коэффициент слишком высокий
- Слишком много high-variance сценариев
- single-source и fallback-кандидаты периодически выглядели как “лучшая ставка”

## Operational проблемы
- бывают пустые прогоны из-за publish window
- бывают прогоны без полноценного odds-источника
- отсутствуют жёсткие alert'ы по integrity и suspicious candidate ratio
