# Cleanup handicap selection + Polish i18n

## Что исправляет

Последний Telegram показал:

```text
🎯 Ставка: Фора 2(0) — Gornik Leczna (0)
```

После патча будет:

```text
🎯 Ставка: Фора 2(0) — Гурник Ленчна
```

Также переводится матч и турнир:

```text
MKS Znicz Pruszkow — Gornik Leczna
Poland - I Liga
```

станет:

```text
Знич Прушкув — Гурник Ленчна
Польша - Первая лига
```

## Что изменено

1. Для DNB/фор выбранная команда очищается от дублирующего handicap point:
   - `(0)`
   - `0`
   - `(+1.5)`
   - `-1.5`

2. Команда после тире дополнительно проходит через Telegram-normalizer.

3. Добавлены словари для Польши:
   - Znicz Pruszkow
   - Gornik Leczna
   - Poland - I Liga
   - несколько частых команд I Liga.

## Как применить

1. Распакуй архив в корень репозитория.
2. Проверь diff в GitHub Desktop.
3. Commit + push.
4. Запусти обычный `Run bot` с profile `balanced`.
