# Clean archive: Russian Telegram + detailed run report

Архив содержит только готовые изменённые и новые файлы. Нет patch-скриптов.

## Что исправлено в v2

В логе было видно, что `apply_global_telegram_i18n_patch.py` перетирал новый `app/services/telegram_i18n.py` старой частичной версией. В v2 это отключено: workflow больше не вызывает перезапись, а сам скрипт сделан no-op для безопасности.

## Что добавлено

### 1. Полная русификация Telegram

Файл:

```text
app/services/telegram_i18n.py
```

Он переводит:

- названия команд;
- названия лиг;
- рынки/ставки;
- причины отказа;
- служебный текст.

Если точного alias нет, латинское название автоматически транслитерируется в кириллицу.

Редактируемый словарь:

```text
config/telegram_i18n_aliases.json
```

Туда можно добавлять точные переводы команд и лиг без изменения Python-кода.

### 2. Подробный отчёт о каждом run

Файл:

```text
scripts/build_detailed_run_report.py
```

Он показывает:

- сколько матчей увидел скрипт;
- сколько матчей с линиями;
- сколько контекстов построено;
- сколько кандидатов проверено;
- основные причины отказа;
- какие пограничные прогнозы не прошли;
- почему именно они не прошли;
- API/grant по ключевым провайдерам.

Scheduled run отправляет подробный отчёт в Telegram только если прогноз не опубликован.
Manual/push создают artifact, но не спамят Telegram.

### 3. Дневной отчёт на русском

Файл:

```text
scripts/build_daily_ops_report.py
```

Исправлено:

- русские команды/лиги;
- `доступно = банк - открытый риск`;
- pending сегодня отдельно от старых pending;
- старый базовый daily Telegram отключён, чтобы не было англо-сообщений.

## Файлы архива

```text
.github/workflows/run-bot.yml
.github/workflows/daily-report.yml
app/services/telegram_i18n.py
config/final_runtime_overrides.env
config/telegram_i18n_aliases.json
scripts/build_detailed_run_report.py
scripts/build_daily_ops_report.py
docs/CLEAN_ARCHIVE_README.md
PATCH_MANIFEST.json
```


## Проверка после применения v2

В detailed run report причины должны быть русскими, например:

```text
• отрицательная контрольная ценность
• слишком большой разрыв между моделью и xG
• закрытая семья рынка: форы
```

Команды из текущего лога должны стать:

```text
Сантос Лагуна — Монтеррей
Льянерос — Альянса Вальедупар
Такома Дифайенс — Лос-Анджелес 2
```
