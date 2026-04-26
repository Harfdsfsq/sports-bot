# Clean changed-files archive

Архив содержит только готовые изменённые и новые файлы. Никаких patch-скриптов, пушей, GitHub API или автозамен.

## Файлы

```text
.github/workflows/run-bot.yml
.github/workflows/daily-report.yml
config/final_runtime_overrides.env
scripts/build_daily_ops_report.py
docs/CLEAN_ARCHIVE_README.md
PATCH_MANIFEST.json
```

## Что изменено

### Run bot

Forecast schedule:

```yaml
- cron: '14,44 3-20 * * *'
```

Логика no-pick отчётов:

```text
schedule -> CONTROLLED_FALLBACK_SEND_NO_PICK_REPORT=true
workflow_dispatch/push -> CONTROLLED_FALLBACK_SEND_NO_PICK_REPORT=false
```

Manual/push могут публиковать валидные прогнозы, но не спамят Telegram отчётом “прогнозов не было”.

### Daily report

Расписание:

```yaml
- cron: '55 20 * * *' # 23:55 MSK, текущий день
- cron: '40 23 * * *' # 02:40 MSK, финальный settlement прошлого дня
```

Добавлен operational report:

```bash
python scripts/build_daily_ops_report.py --send-telegram
```

### Runtime overrides

Добавлен новый файл:

```text
config/final_runtime_overrides.env
```

Он применяется после основных профилей и содержит:

```env
CONTROLLED_FALLBACK_PROXY_SINGLE_SOURCE_MIN_CONFIDENCE=73.5
CONTROLLED_FALLBACK_PROXY_SINGLE_SOURCE_MIN_EDGE_PP=5.0
CONTROLLED_FALLBACK_PROXY_SINGLE_SOURCE_MIN_EV_PCT=10.0
FUTRIXMETRICS_PER_RUN_MAX=2
FUTRIXMETRICS_MIN_SPACING_MINUTES=15
```

Большие `balanced_output.env` и `api_quota_governor.env` не заменяются.

## Как применить

Распаковать архив в корень локального `sports-bot` с заменой файлов, затем проверить diff в GitHub Desktop и сделать commit/push вручную.


## v2 changes after 26/27.04 logs

- Forecast cron moved from `14,44` to `4,34` to absorb GitHub queue delay and the 7-10 minute pipeline.
- Run-bot no-pick Telegram reports are disabled for all event types. Real picks still publish.
- Daily operations report handles no-pick diagnostics instead of spamming every run.
- Daily report target date is protected against GitHub delay past midnight:
  if a scheduled report starts before `04:00 MSK`, it reports the previous football day.
- Available balance is computed as `current_balance - open_exposure` when the state does not contain `available_balance`.
- Pending list now shows only pending bets for the report date and separates older pending as `старые pending`.
