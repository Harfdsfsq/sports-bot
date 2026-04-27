# Sports-bot autorun coverage policy

Цель: бот должен сканировать матчи часто и с перекрытием окон, чтобы fixture не выпадал из `PUBLISH_WINDOW_HOURS` из-за редкого расписания или задержки GitHub Actions.

## Новая схема

GitHub cron задаётся в UTC:

```yaml
17 0,2,4,6,8,10,12,14,16,18,20,22 * * *
```

Для `Europe/Moscow` это примерно:

```text
03:17, 05:17, 07:17, 09:17, 11:17, 13:17,
15:17, 17:17, 19:17, 21:17, 23:17, 01:17 MSK
```

Scheduled-run использует:

```text
PUBLISH_WINDOW_HOURS=6
MIN_KICKOFF_LEAD_MINUTES=35
MAX_PICKS_PER_RUN=2
```

Так окно каждого scheduled-run перекрывает соседние runs. Если GitHub задержит cron на 30-60 минут, покрытие всё равно не должно иметь дыр.

## Telegram-диагностика

Чтобы frequent autoruns не спамили Telegram no-pick отчётами:

- `workflow_dispatch` всегда может отправить detailed report;
- `push` detailed report не отправляет;
- `schedule` отправляет detailed report только в контрольные локальные часы `09`, `15`, `21`;
- controlled fallback no-pick report отключён, чтобы не дублировать detailed diagnostics.

## Что не меняется

Авторан-политика не ослабляет betting guardrails:

- не меняет confidence/quality/EV thresholds;
- не разрешает proxy single-source;
- не открывает закрытые семьи рынков;
- не вмешивается в auto-learning mode;
- не публикует прогнозы напрямую из `app.cli run-once`.

Публикация остаётся через controlled fallback и существующие guardrails.
