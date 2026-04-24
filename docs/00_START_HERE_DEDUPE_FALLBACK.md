# Duplicate-safe controlled fallback fix

## Что чинит

Предыдущий фикс начал публиковать controlled fallback, но один и тот же кандидат мог уйти повторно в следующем запуске, если матч всё ещё находился в publish-window.

Этот патч добавляет idempotency:

- строит стабильный `dedupe_key` по `match_key + family + selection + point + team_side`;
- записывает отправленные fallback-сигналы в `.data/fallback-sent-index.json`;
- проверяет `.data/state.json` и уже опубликованные fallback-записи;
- блокирует повторную публикацию того же рынка на тот же матч в течение `CONTROLLED_FALLBACK_DEDUPE_TTL_HOURS`;
- если повтор заблокирован и новых безопасных кандидатов нет, отправляет единый no-pick report.

## Как применить

1. Распаковать архив в корень репозитория.
2. Проверить diff в GitHub Desktop.
3. Commit + push.
4. Запустить обычный workflow `Run bot` с profile `balanced`.

## Ожидаемое поведение

Если снова появится тот же `Kolos Kovalivka — SC Poltava / Kolos Kovalivka ТМ 1.5`, бот не должен публиковать его повторно.
В `controlled-fallback-report.json` причина будет примерно:

```text
duplicate_fallback_sent_index
```

или

```text
duplicate_state:fallback_published_candidates
```

## Настройки

```env
CONTROLLED_FALLBACK_DEDUPE_ENABLED=true
CONTROLLED_FALLBACK_DEDUPE_TTL_HOURS=72
CONTROLLED_FALLBACK_SENT_INDEX_PATH=.data/fallback-sent-index.json
```
