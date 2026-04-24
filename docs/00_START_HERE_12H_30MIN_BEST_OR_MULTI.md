# 12h / 30min best-or-multi mode

## Режим, который ты попросил

Каждый запуск бота теперь работает так:

```text
искать матчи только от 30 минут до 12 часов
отправлять 1 лучший прогноз
или multi-прогноз, если есть несколько сильных разных матчей
```

## Главные настройки

```env
PUBLISH_WINDOW_HOURS=12
MIN_KICKOFF_LEAD_MINUTES=30
CONTROLLED_FALLBACK_USE_MANUAL_LATE_LEAD=false

MAX_PICKS_PER_RUN=3
CONTROLLED_FALLBACK_MAX_PICKS_PER_RUN=3
CONTROLLED_FALLBACK_MAX_PICKS_PER_MATCH=1
```

## Что значит “1 лучший или multi”

Бот всегда сортирует кандидатов по силе сигнала.

- Если реально сильный кандидат только один — Telegram получает 1 прогноз.
- Если сильных кандидатов 2–3 и они на разные матчи — Telegram получает multi-прогноз одним сообщением.
- Если второй/третий кандидат слабее усиленных требований — он не добавляется.

Для дополнительных ставок действуют усиленные пороги:

```env
CONTROLLED_FALLBACK_EXTRA_PICK_MIN_EV_PCT=7.0
CONTROLLED_FALLBACK_EXTRA_PICK_MIN_EDGE_PP=3.0
CONTROLLED_FALLBACK_EXTRA_PICK_MIN_CONFIDENCE=67.0
```

## Что не ослаблено

По-прежнему не публикуются:

- Tier C;
- single-book proxy;
- negative EV;
- DNB outlier;
- дубли;
- матчи ближе 30 минут;
- матчи дальше 12 часов;
- ставки без sanity-check.

## Что изменено в GitHub Actions

Manual defaults теперь:

```text
publish_window_hours = 12
max_picks_per_run = 3
late_manual_mode = false
```

Также добавлен шаг, который принудительно фиксирует:

```text
PUBLISH_WINDOW_HOURS=12
MIN_KICKOFF_LEAD_MINUTES=30
```

Это защищает от случайного запуска со старым режимом 24/30 часов или lead 10/20 минут.

## Как применить

1. Распакуй архив в корень репозитория.
2. Проверь diff в GitHub Desktop.
3. Commit + push.
4. Запусти обычный `Run bot` с profile `balanced`.

Для ручного запуска оставь:

```text
publish_window_hours = 12
max_picks_per_run = 3
late_manual_mode = false
```
