# API quota spending strategy

## Цель

Получить максимум матчей и контекста без сжигания бесплатных лимитов.

Главный принцип: широкое покрытие дают дешёвые/high-quota источники, дорогие/monthly источники работают только на shortlist.

## Воронка

1. **Все матчи и базовые odds**
   - odds-api.io
   - bzzoiro
   - TheSportsDB / football-data.org, если есть cache и нормальный match mapping

2. **Shortlist / near-miss**
   - api-football
   - SStats
   - FutrixMetrics
   - weather overlays

3. **Финальное подтверждение**
   - OddsPapi
   - OddsFeed
   - Sportsbook API
   - NewsAPI / GNews
   - Meteostat fallback

4. **Не использовать массово**
   - FreeAPILiveFootballData: 100/month, только диагностика
   - SportAPI7: текущий sample endpoint не футбольный
   - AllSportsAPI: лимит неизвестен, держать консервативно

## Провайдеры

| API | Квота | Роль | Бюджет в patch |
|---|---:|---|---:|
| odds-api.io | 100/hour, 2 bookmakers | основной odds/bootstrap | 384 token/day, 8/run |
| bzzoiro | no rate limits | широкий прогнозный context | 10000/day, 50/run |
| api-football | 100/day, 10/min | shortlist predictions/context | 78/day, 3/run |
| football-data.org | 10/min registered | standings/history | 220/day, 8/run |
| TheSportsDB | 30/min | broad context | 360/day, 12/run |
| WeatherAPI + OWM | 100K/month + 1000/day | weather for totals/BTTS | 360/day, 6/run |
| FutrixMetrics | 5000/month | shortlist form/performance | 110/day, 5/run |
| NewsAPI | 100/day | injury/news only | 34/day, 1/run |
| GNews | 100/day | injury/news only | 34/day, 1/run |
| Currents | 1000/day | future primary news | not enabled: provider absent |
| OddsPapi | 250/month | secondary odds confirmation | 8/day, 1/run |
| OddsFeed | 500/month | late market confirmation | 12/day, 1/run |
| Sportsbook API | 50/day | market anomaly only | 18/day, 1/run |
| Meteostat | 500/month | weather fallback | 10/day, 1/run |
| FreeAPILiveFootballData | 100/month | diagnostics only | 3/day, 1/run |
| AllSportsAPI | unknown | conservative supplement | 12/day, 1/run |

## Почему так

- `odds-api.io` имеет hourly quota, значит его можно использовать каждый run. Но аккаунт ограничен 2 букмекерами, поэтому `ODDS_API_IO_BOOKMAKERS=Bet365,Unibet` не расширяется.
- `bzzoiro` без лимитов должен быть максимально активен.
- `api-football` нельзя тратить на весь slate: 100/day быстро сгорают. Поэтому 3/run и shortlist.
- `OddsPapi`, `OddsFeed`, `Meteostat`, `FreeAPILiveFootballData` месячные — только подтверждение, не массовый сбор.
- News API не должны быть quality gate для каждой игры. Их роль — травмы/новости по top near-miss.

## Ожидаемая проверка

После запуска:

```text
.data/exports/latest-provider-quota-governor.json
```

Ключевые провайдеры должны иметь `granted > 0`:

```text
odds_api_io
bzzoiro
sstats
api_football
football_data
thesportsdb
weather
```

Если `odds_api_io granted=0`, бот снова будет видеть мало матчей.
