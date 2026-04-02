# Sports Bot Final Bundle

Этот архив содержит актуальный набор файлов для замены в репозитории.

## Что включено
- `app/` — актуальные Python-файлы бота
- `.github/workflows/run-bot.yml` — workflow с исправленным шагом коммита (`git add -A .data`)
- `apps_script/sheet_sync.gs` — синхронизация экспорта в Google Sheets через Apps Script
- `docs/` — пояснения и отчёты
- `originals/bets_full_upgraded_v15.gs` — старый Apps Script как референс

## Что уже учтено
- фиксы падений в `bookies_api`, `api_football`, обработке процентов
- `odds_api_io` настроен под `Bet365,Unibet`
- ограничение запросов к API-Football
- анти-overconfidence для xg-модели
- фикс финального шага workflow, чтобы job не падал на отсутствующих optional `.data` файлах

## Как применить
1. Распакуй архив.
2. Скопируй содержимое в корень локального репозитория с заменой файлов.
3. Проверь diff в GitHub Desktop.
4. Закоммить и запусти workflow.

## Что ещё может требовать донастройки
- рассинхрон счётчика `published` и количества ставок в Telegram
- дальнейшая калибровка `xg_total`/`xg_spread`
