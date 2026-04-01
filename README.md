# Sports Value Bot Rewrite - Free GitHub Actions Edition

Это бесплатная версия бота без Render и без PostgreSQL.

Что изменено:
- убран обязательный Postgres;
- состояние хранится в `.data/state.json`;
- запуск идёт через GitHub Actions по расписанию;
- после каждого запуска workflow коммитит обновлённый state обратно в репозиторий;
- Telegram-публикация работает через GitHub Secrets.

## Что нужно сделать

### 1. Добавить secrets в GitHub
В репозитории открой:
`Settings -> Secrets and variables -> Actions -> New repository secret`

Добавь такие secrets:
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`
- `THE_ODDS_API_KEY`
- `ODDS_API_IO_KEY`
- `SSTATS_API_KEY`

### 2. Включить GitHub Actions
Открой вкладку `Actions` и разреши запуск workflow, если GitHub попросит.

### 3. Запускать вручную или по расписанию
Файл workflow уже лежит тут:
`/.github/workflows/run-bot.yml`

Он умеет:
- запускаться каждые 30 минут;
- запускаться вручную через `Run workflow`.

## Как проверить первый запуск
1. Перейди во вкладку `Actions`.
2. Открой workflow `Run Sports Bot`.
3. Нажми `Run workflow`.
4. Дождись окончания.
5. Проверь, пришло ли сообщение в Telegram.
6. Проверь, появился ли файл `.data/state.json` или обновился ли он.

## Где хранится история
История хранится в файле:
`.data/state.json`

Там будут:
- последние запуски;
- уже опубликованные ставки;
- защита от повторной публикации тех же ставок.

## Локальный запуск
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python -m app.cli run-once
```

## FastAPI
Если захочешь локально web-интерфейс:
```bash
uvicorn app.main:app --reload
```

Эндпоинты:
- `GET /health`
- `POST /run`

## Ограничения бесплатной версии
- нет PostgreSQL;
- нет CLV/settlement worker;
- state хранится в git-файле, а не в нормальной БД;
- история зависит от того, что workflow может коммитить обратно в репозиторий.

Но для бесплатного старта это самый простой и рабочий вариант.
