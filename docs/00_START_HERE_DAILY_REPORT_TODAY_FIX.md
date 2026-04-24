# Daily report today-date fix

## Проблема

В 23:04 бот отправил:

```text
📊 Итоги прогнозов за 23.04.2026
```

хотя запуск был 24.04.2026 23:04 MSK.

Причина в `app/state.py`:

```python
offset_days = max(0, int(getattr(settings, 'daily_report_target_offset_days', 1) or 1))
```

Из-за `or 1` значение `DAILY_REPORT_TARGET_OFFSET_DAYS=0` превращалось в `1`.

## Что исправлено

Добавлен runtime patch:

```text
scripts/apply_daily_report_today_patch.py
```

Он меняет логику так, что `0` остаётся `0`:

```python
raw_offset = getattr(settings, 'daily_report_target_offset_days', 0)
if raw_offset in (None, ''):
    raw_offset = 0
offset_days = max(0, int(raw_offset))
```

## Зафиксировано

```env
DAILY_REPORT_ENABLED=true
DAILY_REPORT_SEND_TELEGRAM=true
DAILY_REPORT_HOUR_LOCAL=22
DAILY_REPORT_TARGET_OFFSET_DAYS=0
DAILY_REPORT_RESEND_ON_CHANGE=false
```

## Ожидаемый результат

Если запуск в Москве:

```text
24.04.2026 23:04 MSK
```

то заголовок будет:

```text
📊 Итоги прогнозов за 24.04.2026
```

## Как применить

1. Распакуй архив в корень репозитория.
2. Проверь diff в GitHub Desktop.
3. Commit + push.
4. Запусти `Run bot` после 22:00 MSK или дождись scheduled run.
