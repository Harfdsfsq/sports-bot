# Run bot report-sync fix

Этот пакет исправляет не стратегию fallback, а порядок Telegram-сообщений.

## Проблема

До фикса обычный `Run bot` сначала отправлял отчёт:

> прогнозов не было

а затем отдельный скрипт `controlled fallback` отправлял прогноз. В Telegram это выглядело противоречиво.

## Что изменено

- `RUN_REPORT_ENABLED=false` в workflow/profile.
- Основной бот по-прежнему может публиковать нормальные picks.
- Если нормального pick нет, `scripts/publish_controlled_fallback.py`:
  - либо публикует controlled fallback;
  - либо отправляет единый no-pick report, если fallback тоже не нашёл безопасный вариант.
- В artifact всегда сохраняется `controlled-fallback-report.json`.

## Как применять

1. Распаковать архив в корень репозитория.
2. Проверить diff в GitHub Desktop.
3. Commit + push.
4. Запустить обычный workflow **Run bot** с profile `balanced`.

## Ожидаемый результат

В Telegram больше не должно быть пары сообщений:

1. `прогнозов не было`
2. `контролируемый прогноз`

Теперь будет либо normal pick, либо controlled fallback, либо один финальный no-pick report.
