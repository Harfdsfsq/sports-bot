# Top frequency final

## Цель

Сделать так, чтобы топовые ставки приходили чаще, но не за счёт мусорных прогнозов.

## Что меняется

### 1. До 3 сильных ставок за один запуск

Раньше controlled fallback публиковал только один лучший прогноз:

```text
1 прогноз за run
```

Теперь balanced-профиль может публиковать до 3 разных матчей:

```env
CONTROLLED_FALLBACK_MAX_PICKS_PER_RUN=3
CONTROLLED_FALLBACK_MAX_PICKS_PER_MATCH=1
```

Каждая ставка всё равно отдельно проходит:
- canonical EV;
- edge;
- проверку времени;
- dedupe;
- 2+ линии;
- sanity-check рынка;
- DNB outlier guard;
- proxy+single-source strict guard.

### 2. Дополнительные ставки должны быть сильными

Вторая и третья ставка проходят отдельный усиленный фильтр:

```env
CONTROLLED_FALLBACK_EXTRA_PICK_MIN_EV_PCT=7.0
CONTROLLED_FALLBACK_EXTRA_PICK_MIN_EDGE_PP=3.0
CONTROLLED_FALLBACK_EXTRA_PICK_MIN_CONFIDENCE=67.0
```

То есть бот не будет добивать список слабыми кандидатами.

### 3. Окно balanced расширено до 30 часов

```env
PUBLISH_WINDOW_HOURS=30
```

Это даёт больше матчей в пуле, но не ломает качество, потому что финальные guard'ы сохранены.

### 4. Риск ограничен

```env
CONTROLLED_FALLBACK_TOTAL_STAKE_CAP_PCT=1.8
CONTROLLED_FALLBACK_MAX_STAKE_TIER_A=7.5
CONTROLLED_FALLBACK_MAX_STAKE_TIER_B=5.12
CONTROLLED_FALLBACK_TIER_C_PUBLISH_ENABLED=false
```

Даже если бот нашёл 2–3 ставки, общий риск остаётся ограниченным.

### 5. Больше кандидатов в rescue-pool

```env
MAX_INTERNAL_CANDIDATES_PER_RUN=80
MAX_CANDIDATES_PER_MATCH_PRE_FILTER=10
MAX_RESCUE_CANDIDATES_PER_MATCH=16
MAX_RESCUE_PREFILTER_CANDIDATES_EXPORT=750
```

Это повышает шанс найти хорошие сигналы, не ослабляя финальный фильтр.

## Что НЕ меняется

По-прежнему не публикуются:

- Tier C;
- single-book proxy;
- отрицательный EV;
- DNB outlier;
- матч без времени;
- дубль;
- ставка без рыночного/xG sanity подтверждения.

## Как применить

1. Распакуй архив в корень репозитория.
2. Проверь diff в GitHub Desktop.
3. Commit + push.
4. Запусти `Run bot` с profile `balanced`.
5. Для ручного запуска оставь:
   - `publish_window_hours = 30`
   - `max_picks_per_run = 3`

## Ожидаемый результат

Если в пуле есть 2–3 сильных разных матча, Telegram получит одно компактное сообщение:

```text
🔥 2 топовых контролируемых прогноза на ближайшие 30 часов
...
1. Матч A
...
2. Матч B
...
```

Если сильный матч только один — придёт один прогноз. Если сильных нет — no-pick с наблюдениями.
