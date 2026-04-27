# v15 API policy — all providers except api-football

## Что изменено

- `api-football` полностью удалён из активного runtime:
  - ключ не передаётся в workflow;
  - все `API_FOOTBALL_*` env-признаки обнуляются;
  - `app/providers/api_football.py` заменён на безопасный stub без HTTP-запросов;
  - в `provider_request_budget.json` его нет среди рабочих providers.
- Остальные API включены, но через request budget:
  - broad coverage: `odds-api.io`, `bzzoiro`;
  - context: `SStats`, `football-data.org`, `TheSportsDB`, `ESPN`, `OpenFootball`;
  - news: `Currents`, `NewsAPI`, `GNews`;
  - weather: `WeatherAPI`, `OpenWeatherMap`, `Meteostat`;
  - sparse/secondary: `AllSportsAPI`, `OddsPapi`, `FutrixMetrics`, `SportAPI`, `Sportsbook API`, `FreeAPILiveFootballData`, `OddsFeed`.

## Важные лимиты

| API | Режим |
|---|---|
| odds-api.io | 8/run, 2 букмекера: Bet365+Unibet |
| Currents | до 2/run в news provider |
| NewsAPI | 1/run fallback |
| GNews | 1/run, sparse slots |
| AllSportsAPI | 1/run, sparse slots, unknown free limit |
| OddsPapi | 1/run, 150/month safe budget, cooldown on REQUEST_LIMIT_EXCEEDED |
| FutrixMetrics | 1/run, shortlist, 720/month safe budget |
| football-data.org | 4/run operating budget |
| TheSportsDB | 6/run |
| WeatherAPI | 4/run + cache |
| OpenWeatherMap | 2/run fallback |
| Sportsbook API | 1/run sparse |
| Meteostat | 1/run sparse fallback |
| FreeAPILiveFootballData | 1/run, 60/month safe budget |
| OddsFeed | 1/run, 180/month safe budget |

## Проверка после применения

В логах должно быть:

```text
PROVIDER_REQUEST_BUDGET_VERSION=v15-all-api-budget-no-api-football
API_FOOTBALL_REQUEST_BUDGET_REASON=removed_from_project
```

И в diagnostics:

```text
api_football requests=0
```

Для API без заданного GitHub secret ожидаемо будет `api_key_present=false` или `requests=0`.
