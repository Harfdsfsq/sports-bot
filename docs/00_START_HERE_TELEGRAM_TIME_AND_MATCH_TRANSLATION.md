# Telegram time and match translation fix

## Что исправляет

1. Время матча в Telegram больше не выводится как UTC ISO:

```text
2026-04-24T15:10:00+00:00
```

Теперь формат:

```text
24.04.2026 18:10 MSK
```

Таймзона задаётся через:

```env
TELEGRAM_MATCH_TIMEZONE=Europe/Moscow
TELEGRAM_SHOW_UTC_MATCH_TIME=false
```

2. Добавлен перевод команд и лиг для ОАЭ:

```text
Dubai United FC — Gulf United
```

становится:

```text
Дубай Юнайтед — Галф Юнайтед
```

```text
United Arab Emirates - First Division
```

становится:

```text
ОАЭ - Первый дивизион
```

3. Перевод работает именно перед отправкой Telegram-сообщения в `scripts/publish_controlled_fallback.py`.

## Как применить

1. Распакуй архив в корень репозитория.
2. Проверь diff в GitHub Desktop.
3. Commit + push.
4. Запусти обычный `Run bot` с profile `balanced`.

## Ожидаемый Telegram-вид

```text
1. Дубай Юнайтед — Галф Юнайтед
🏆 Турнир: ОАЭ - Первый дивизион
🕒 Начало: 24.04.2026 18:10 MSK
```
