# Executive summary

## Ключевой вывод
Главная проблема бота — не недостаточная строгость фильтров, а **рассогласованность данных внутри кандидатов**.

Типовые симптомы:
- `odds` не согласован с `implied_probability`
- `adjusted_probability` не согласован с `source_summary.adjusted_probability`
- `edge_pct` и `ev_pct` иногда считаются из разных probability-полей
- fallback / historical relief сигналы доходят до публикационного контура
- средний исторический коэффициент слишком высокий, стратегия убыточна

## Что это означает
Пока не будет одного канонического источника истины для:
- selected odds
- selected implied probability
- market probability
- fair odds from market
- adjusted probability
- EV / edge

бот нельзя считать готовым к боевому режиму.

## Порядок ремонта
1. Починить env/workflow и single fixed run
2. Ввести canonical candidate fields
3. Добавить hard reject на integrity mismatch
4. Запретить fallback-публикации в main
5. Сузить боевые рынки и диапазон коэффициентов
6. Закрыть тесты и ввести monitoring/alerts
