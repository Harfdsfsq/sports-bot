# Final quality hardening

## Что показал последний прогон

Свежий report показал:

- резервных кандидатов: `130`;
- реально прошли только `2`;
- оба были `уровень C`;
- оба были `books_count=1`;
- оба были `quality_score_source=proxy`.

Опубликованный прогноз:

```text
Skive IK — BK Fremad Amager
ТМ(2)
books: 1
quality: proxy
tier: C
```

Математически xG его поддерживал, но для публичного Telegram-прогноза это всё ещё слабая основа: одна линия + резервная оценка качества.

## Что меняет пакет

### 1. Tier C больше не публикуется в Telegram по умолчанию

```env
CONTROLLED_FALLBACK_TIER_C_PUBLISH_ENABLED=false
```

Tier C остаётся в отчёте как наблюдение, но не уходит как прогноз.

### 2. Single-book + proxy-quality запрещён для Telegram

```env
CONTROLLED_FALLBACK_REQUIRE_2_BOOKS_FOR_TELEGRAM=true
CONTROLLED_FALLBACK_REJECT_PROXY_SINGLE_BOOK=true
CONTROLLED_FALLBACK_REQUIRE_MARKET_CONFIRMATION_FOR_PROXY=true
```

Это главный фикс против слабых прогнозов.

### 3. No-pick report теперь полезнее

Если бот нашёл value, но не опубликовал из-за качества, Telegram покажет блок:

```text
Наблюдения без публикации:
• Матч: рынок, EV +..., линий 1 — не публикую: одна линия + резервная оценка качества
```

Так ты видишь, что бот работает, но не ставит мусор.

### 4. Наполнение не урезано

Пул расширен:

```env
MAX_INTERNAL_CANDIDATES_PER_RUN=64
MAX_CANDIDATES_PER_MATCH_PRE_FILTER=8
MAX_RESCUE_CANDIDATES_PER_MATCH=14
MAX_RESCUE_PREFILTER_CANDIDATES_EXPORT=500
```

Но публикация стала строже.

### 5. Добавлен перевод Дании

```text
Skive IK — BK Fremad Amager
Denmark - 2nd Division
```

станет:

```text
Скиве — Фремад Амагер
Дания - Второй дивизион
```

## Итоговая политика

- Tier A: публикуется, если есть реальное качество, 2+ линии и sanity-check.
- Tier B: публикуется как контролируемый резерв, но только с 2+ линиями и нормальным EV.
- Tier C: watch-only, не Telegram-прогноз.
- Single-book proxy: watch-only.
- Negative EV: hard reject.

## Как применить

1. Распакуй архив в корень репозитория.
2. Проверь diff в GitHub Desktop.
3. Commit + push.
4. Запусти обычный `Run bot` с profile `balanced`.
5. Пришли `run-bot-current`.
