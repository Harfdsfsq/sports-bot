# Run bot normal fix

Этот пакет правит обычный workflow `Run bot`, без fixed-run и без экспериментальных workflow.

## Что исправлено

1. Обычный `Run bot` теперь всегда собирает `run-bot-current` artifact.
2. В artifact попадает `artifacts/run-bot-bundle.zip`.
3. После каждого запуска формируется `latest-candidate-integrity.json`.
4. Профили `balanced` и `conservative` запрещают emergency/last-resort публикации в main.
5. Manual запуск по умолчанию использует окно 24 часа, чтобы поздний диагностический запуск не был пустым.
6. Scheduled запуск оставлен на 12 часов.

## Как запускать

1. Распаковать архив в корень репозитория.
2. Commit + push через GitHub Desktop.
3. GitHub Actions -> `Run bot`.
4. Первый ручной запуск:
   - profile: `balanced`
   - dry_run: `false`
   - publish_window_hours: `24`
   - max_picks_per_run: `1`

## Что присылать для анализа

Скачай artifact `run-bot-current` и загрузи сюда:
- `artifacts/run-bot-bundle.zip`

## Почему это нужно

Последние логи показали:
- диагностический workflow шёл с `PUBLISH_DRY_RUN=true`;
- поздний запуск видел 93 матча до окна, но 0 после publish-window;
- `odds_api_io` в части запусков был без ключа;
- обычный `Run bot` не собирал удобный единый bundle и integrity report.

Этот fix делает обычный `Run bot` основной точкой работы и диагностики.
