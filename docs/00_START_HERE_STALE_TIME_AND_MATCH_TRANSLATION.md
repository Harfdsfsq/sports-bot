# Stale-match guard + русский перевод матчей

## Что исправляет пакет

1. Резервный публикователь больше не может отправить старый матч.
   - Если `commence_time` уже прошёл — кандидат получает отказ `матч уже начался`.
   - Если матч не попадает в `PUBLISH_WINDOW_HOURS` — кандидат получает отказ `матч вне текущего окна публикации`.
   - Если времени начала нет — кандидат отклоняется, если `CONTROLLED_FALLBACK_ALLOW_UNKNOWN_TIME=false`.

2. Workflow перед каждым запуском чистит старые latest-файлы:
   - `latest-rescue-candidates.json`
   - `latest-controlled-fallback-report.json`
   - `latest-controlled-fallback-pick.json`

3. Telegram теперь переводит/нормализует названия матчей:
   - `New York City FC — FC Cincinnati` → `Нью-Йорк Сити — Цинциннати`
   - `Kolos Kovalivka — SC Poltava` → `Колос Ковалёвка — Полтава`
   - лиги тоже приводятся к русскому виду, где есть словарь.

## Как применить

1. Распакуй архив в корень репозитория.
2. Проверь diff в GitHub Desktop.
3. Commit + push.
4. Запусти обычный workflow `Run bot` с profile `balanced`.

## Что проверить после запуска

В Telegram не должно быть матчей, которые уже начались или были сыграны раньше текущего окна.
В `controlled-fallback-report.json` должны появиться русские поля:

- `home_team_ru`
- `away_team_ru`
- `league_name_ru`
- `reject_reasons_ru`
