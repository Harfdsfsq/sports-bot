# Separate Daily Report run

## Что добавлено

Добавлен отдельный GitHub Actions workflow:

```text
.github/workflows/daily-report.yml
```

Он запускает только дневной отчёт:

- settlement pending-ставок;
- пересчёт bankroll;
- формирование daily report;
- отправка daily report в Telegram;
- сохранение artifacts.

Он **не запускает controlled fallback** и не отправляет прогноз/no-pick.

## Где запускать

В GitHub:

```text
Actions → Daily report → Run workflow
```

Параметры:

```text
profile = balanced
target_offset_days = 0
force_resend = true
```

`target_offset_days`:

```text
0 = отчёт за сегодня по MSK
1 = отчёт за вчера
```

## Автоматический запуск

Workflow запланирован:

```text
22:10 MSK
23:10 MSK safety rerun
```

Второй запуск нужен на случай, если часть матчей закрылась чуть позже. Он не должен дублировать отчёт без изменений, но manual `force_resend=true` может принудительно переслать отчёт.

## Защита от отправки прогнозов

В workflow принудительно выставлено:

```env
PREDICTION_PUBLICATION_ENABLED=false
CONTROLLED_FALLBACK_SEND_TELEGRAM=false
RUN_REPORT_ENABLED=false
SETTLEMENT_SEND_TELEGRAM_SUMMARY=false
DAILY_REPORT_ENABLED=true
DAILY_REPORT_SEND_TELEGRAM=true
```

## Исправление даты

Также используется патч:

```text
scripts/apply_daily_report_today_patch.py
```

Он исправляет баг, где `DAILY_REPORT_TARGET_OFFSET_DAYS=0` превращался в `1`.

## Artifacts

После запуска будет artifact:

```text
daily-report-current
```

Внутри:

```text
latest-daily-report.json
latest-daily-summary.json
latest-daily-report.csv
latest-daily-summary.csv
debug-last-run.json
```

## Как применить

1. Распакуй архив в корень репозитория.
2. Проверь diff в GitHub Desktop.
3. Commit + push.
4. В GitHub Actions появится новый workflow `Daily report`.
