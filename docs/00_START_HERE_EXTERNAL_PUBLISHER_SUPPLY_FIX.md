# Run bot external publisher supply fix

## Что исправляет

Последний запуск показал не проблему порогов, а проблему пула кандидатов:

- controlled fallback проверил только 1 кандидата;
- единственный кандидат имел `canonical_ev_pct < 0`;
- публиковать его нельзя.

Этот patch переводит обычный `Run bot` в режим:

1. основной Python-бот строит кандидатов, state, debug и exports;
2. основной бот **не отправляет Telegram напрямую**;
3. `scripts/publish_controlled_fallback.py` собирает расширенный пул:
   - `latest-picks.json`;
   - `debug-last-run.candidates_before_quality`;
   - `debug-last-run.candidates_after_quality`;
   - свежие `shadow_bets` из `.data/state.json`;
4. внешний publisher выбирает 1 ставку только если после canonical-пересчёта EV и edge положительные.

## Что принципиально не ослаблено

- `canonical_negative_value` всё ещё hard reject;
- реальные дубли из `bets` / `published_candidates` блокируются;
- fallback-sent-index блокирует повтор на 72 часа;
- внутренние emergency / historical relief / last resort выключены.

## Как применить

1. Распаковать архив в корень репозитория.
2. Проверить diff в GitHub Desktop.
3. Commit + push.
4. Запустить обычный workflow `Run bot` с profile `balanced`.

## Ожидаемый результат

Если текущий run содержит хотя бы один кандидат с положительным canonical EV, он будет опубликован как controlled forecast.

Если все кандидаты отрицательные, бот корректно отправит no-pick report, но report теперь покажет расширенный пул кандидатов по источникам.
