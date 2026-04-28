# sports-bot

Telegram-бот для ежедневных футбольных прогнозов. Бот собирает список матчей, подтягивает коэффициенты и контекст, строит кандидатов, пропускает их через quality-guards и публикует контролируемый топ прогнозов в Telegram.

## Что делает бот

- запускается каждые 2 часа по MSK через `.github/workflows/run-bot.yml`;
- в 00:00 MSK обновляет дневной список матчей и дальше дополняет данные при каждом прогоне;
- держит дневную цель около 5 прогнозов через `VOLUME_POLICY_MODE=target_5`;
- публикует прогнозы небольшими партиями: обычно 1-2 за один запуск;
- после каждого рабочего запуска отправляет подробный отчёт о run в Telegram;
- сохраняет состояние, отчеты и выгрузки в `.data/exports` и `.logs`;
- отправляет прогнозы и операционные отчеты в Telegram;
- ведет learning/quality-отчеты для дальнейшей настройки порогов.

## Быстрый запуск локально

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m app.cli run-once
```

На Windows:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m app.cli run-once
```

Для реальной публикации нужны переменные окружения или GitHub Secrets:

- `TELEGRAM_TOKEN`
- `TELEGRAM_CHAT_ID`
- `ODDS_API_IO_KEY`
- дополнительные API-ключи из workflow: `ALLSPORTSAPI_API_KEY`, `ODDSPAPI_API_KEY`, `FUTRIXMETRICS_API_KEY`, `FOOTBALL_DATA_API_KEY`, `GNEWS_KEY`, `NEWSAPI_KEY`, weather/RapidAPI ключи.

## Основные команды

```bash
python -m app.cli run-once
python -m app.cli coverage-audit
python -m app.cli history-guard-audit
python -m app.cli training-dataset
python -m app.cli reporting-sqlite
```

## Как получить около 5 прогнозов сегодня

1. Откройте GitHub Actions.
2. Запустите workflow `run-bot` вручную.
3. Оставьте `volume_mode=target_5`.
4. Один запуск публикует максимум 1-2 прогноза, чтобы бот выбирал лучшие ставки в течение дня, а не отправлял все сразу.
5. Если после запуска опубликовано меньше 5 прогнозов, дождитесь следующего планового прогона или запустите workflow позже: бот доберет только новые ставки, которые проходят финальные guards.
6. Дневной hard cap остается 7, чтобы не разгонять объем в слабый день.

Качество не отключается: Tier C не публикуется, финальные проверки по линиям, EV, edge, xG/BTTS/DNB и дублям остаются включенными.

## Важные файлы

- `.github/workflows/run-bot.yml` - основной автозапуск и ручной запуск прогнозов.
- `.github/workflows/daily-report.yml` - ежедневный отчет и settlement.
- `app/services/runner.py` - основной pipeline.
- `app/services/model.py` - построение кандидатов.
- `app/services/quality.py` - quality layer.
- `scripts/apply_daily_top5_publish_policy.py` - дневная политика top-5.
- `scripts/publish_controlled_fallback.py` - финальная публикация контролируемых прогнозов.
- `config/volume_policy.json` - профили target_3/target_5/target_7.
- `UPDATE` - текущий план развития.

## Что не хранить в репозитории

Не коммитьте `.env`, `.logs`, `artifacts`, `__pycache__`, локальные базы и временные выгрузки. Они уже закрыты `.gitignore`.
