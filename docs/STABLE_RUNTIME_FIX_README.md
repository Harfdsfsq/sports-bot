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


## Auto-learning layer

Добавлен безопасный модуль автообучения:

```bash
python scripts/auto_learning_engine.py
```

Он учится только на закрытых ставках и сохраняет результат в:

```text
.data/learning-state.json
.data/auto_learning_runtime_overrides.env
.data/calibration-profile.json
```

Workflow теперь применяет `.data/auto_learning_runtime_overrides.env` в начале следующего run.

Важно: автообучение не ослабляет фильтры автоматически. Оно может только наблюдать или ужесточать proxy/single-source guard’ы после достаточной плохой статистики.
