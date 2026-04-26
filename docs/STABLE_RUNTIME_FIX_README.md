# Stable runtime fix

Этот чистый архив чинит две проблемы из последнего лога:

1. workflow падал на последнем шаге из-за git conflict в `.data/state.json`;
2. manual run строил подробный отчёт, но не отправлял его в Telegram.

## Главное изменение

Вместо прямого git commit/pull/push теперь используется:

```bash
python scripts/sync_persistent_state.py || true
```

State-sync стал best-effort и не может завалить run.

## Telegram

`workflow_dispatch` теперь получает:

```text
DETAILED_RUN_REPORT_SEND_TELEGRAM=true
```

`push` остаётся без no-pick Telegram-спама.

## Файлы

См. `PATCH_MANIFEST.json`.
