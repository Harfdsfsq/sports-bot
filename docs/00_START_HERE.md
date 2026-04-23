# Что это

Это overlay для локальной замены поверх репозитория.

Цель:
- оставить single-run режим;
- не пускать fallback-сигналы в основной канал;
- публиковать в Telegram только чистый main-pass;
- собирать один bundle с итогами запуска.

## Как применять
1. Распакуй архив в корень репозитория.
2. Проверь diff в GitHub Desktop.
3. Commit + Push.
4. Запусти workflow `Run bot • main clean single run`.
