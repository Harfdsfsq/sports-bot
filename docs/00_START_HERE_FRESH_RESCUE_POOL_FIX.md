# Fresh rescue pool fix

## Что показал последний запуск

Резерв проверил 50 кандидатов, но 49 из них пришли из `state_shadow_bets` и уже начались:

- `state_shadow_bets: 49`
- `match_already_started: 49`

Защита от старых матчей работает, но shadow-история забивает текущий пул.

## Что исправляет пакет

1. `state_shadow_bets` больше не используется как источник резервных кандидатов по умолчанию.
2. Во время текущего запуска сохраняется свежий pre-filter rescue pool до `_filter_and_rank()`.
3. Резервный публикователь читает свежий файл:
   - `.data/exports/latest-rescue-candidates.json`
   - `artifacts/run-bot/latest-rescue-candidates.json`
4. Старые матчи по-прежнему жёстко отклоняются.
5. Русский Telegram-текст и перевод матчей остаются.

## Ожидаемый результат

В Telegram-отчёте пул должен выглядеть примерно так:

```text
Пул кандидатов:
• latest_rescue_candidates: 20-200
• debug_candidates_before_quality: ...
```

`state_shadow_bets` не должен появляться, если явно не включить:

```env
CONTROLLED_FALLBACK_INCLUDE_STATE_SHADOW=true
```

## Как применить

1. Распакуй архив в корень репозитория.
2. Проверь diff в GitHub Desktop.
3. Commit + push.
4. Запусти обычный `Run bot` с profile `balanced`.
