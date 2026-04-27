# Provider request budget v14

Цель: не сжигать дневные и месячные API-лимиты на частых авторанах и manual `workflow_dispatch`, но сохранить максимальное покрытие через `odds-api.io` и `bzzoiro`.

## Главные изменения

- `api-football` отключён, пока аккаунт возвращает `Your account is suspended`.
- `OddsPapi`, `AllSportsAPI`, `FreeAPILiveFootballData`, `SportAPI7`, `Currents` выключены.
- `FutrixMetrics`, `NewsAPI`, `GNews`, `Sportsbook`, `Meteostat`, `OddsFeed` не используются на manual-runs и идут только по редким scheduled-слотам.
- `ESPN` не выключен полностью, но сужен до одного slug на run:
  - `ESPN_SLUGS_PER_RUN_LIMIT=1`
  - `ESPN_QUERY_ALL_ALLOWED_WHEN_UNMAPPED=false`
  - `ESPN_MAX_MATCHES=6`
- `SStats` ужат:
  - `SSTATS_CONTEXT_MATCH_LIMIT=6`
  - `SSTATS_LOOKBACK_DAYS=14`
  - `SSTATS_REQUESTS_MAX_PER_RUN=4`

## Нормальная картина после v14

В `latest-provider-request-budget.json`:

- `api_football`: `grant=0`, `disabled_by_policy`
- `oddspapi`: `grant=0`, `disabled_by_policy` или cooldown
- `futrixmetrics` на manual: `grant=0`, `manual_disabled_by_policy`
- `newsapi/gnews/sportsbook/meteostat/oddsfeed` на manual: `grant=0`, `manual_disabled_by_policy`
- `espn_public`: `grant=9`, но фактические запросы должны упасть примерно с 58 до 7-12.

## Что смотреть в следующем run

В `.logs/debug-last-run.json` / Telegram-доставленном detailed report:

- `oddspapi requests: 0`
- `allsportsapi requests: 0`
- `api_football requests: 0`
- `espn scoreboard_requests` около 5, не 50
- `sstats requests` около 4-8, не 12-30
- `futrixmetrics requests` 0 на manual; scheduled только в разрешённые окна.

Если конкретный provider всё равно превышает env-budget, нужен уже точечный provider-code cap.
