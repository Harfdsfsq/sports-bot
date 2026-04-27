# Provider request budget v12

Цель: 12 основных авторанов в сутки не должны сжигать дневные и месячные API-лимиты, но бот должен сохранять максимальное покрытие матчей.

## Схема

1. `scripts/apply_provider_quota_governor.py` оставляет существующий token-bucket слой.
2. `scripts/apply_provider_request_budget.py` применяет второй, более жёсткий pre-run слой по реальным лимитам провайдеров.
3. Скрипт пишет итоговые env-переменные в `$GITHUB_ENV` прямо перед `python -m app.cli run-once`.
4. Состояние budget хранится в `.data/provider_request_budget_state.json` и синхронизируется как persistent state.

## Основная стратегия

- Broad coverage: `odds-api.io` + `bzzoiro`.
- Shortlist-only: `FutrixMetrics`, `NewsAPI`, `GNews`, `OddsPapi`, `Meteostat`, `OddsFeed`, `Sportsbook API`.
- Unknown/too-small providers: `AllSportsAPI`, `FreeAPILiveFootballData`, `SportAPI7` выключены до подтверждения схемы/лимита.
- ESPN/OpenFootball public sources ограничены по HTTP-шуму.

## Лимиты, учтённые в v12

| Provider | User limit | v12 operating budget |
|---|---:|---:|
| Currents News API | 1000/day | disabled; provider not wired |
| GNews | 100/day | 24/day, sparse slots |
| NewsAPI | 100/day | 24/day, sparse slots |
| odds-api.io | 100/hour, 2 bookmakers | 8/run, Bet365+Unibet |
| api-football | 100/day, 10/min | 36/day, 2/run, cooldown on auth |
| AllSportsAPI | unclear trial/free | disabled |
| bzzoiro | no rate limits | broad context, 70/run |
| OddsPapi | 250/month | 150/month, sparse slots, fatal cooldown |
| FutrixMetrics | 5000/month | 2400/month, 2/run shortlist |
| football-data.org | 10/min registered | 96/day, 6/run |
| TheSportsDB | 30/min | 144/day, 8/run |
| WeatherAPI | 100K/month | 9000/month, 4/run |
| OpenWeatherMap | 1000/day, 60/min | 120/day, fallback |
| SportAPI / API-Sports | 100/day | API-Football only; SportAPI7 disabled |
| Sportsbook API | 50/day | 12/day, sparse slots |
| Meteostat | 500/month | 240/month, sparse fallback |
| FreeAPILiveFootballData | 100/month | disabled |
| OddsFeed | 500/month | 240/month, sparse slots |

## Логи

После применения в workflow должен появиться шаг:

```text
Apply provider request budget
```

И артефакт:

```text
.data/exports/latest-provider-request-budget.json
```

Ключевые поля:

- `grant` — сколько запросов/контекстов разрешено провайдеру на текущий run;
- `reason=granted` — провайдер разрешён;
- `reason=slot_not_allowed` — месячный/малый провайдер пропущен в этом слоте;
- `reason=spacing_active` — ещё действует пауза;
- `reason=daily_budget_exhausted` / `monthly_budget_exhausted` — бюджет периода исчерпан;
- `reason=cooldown_active` — провайдер отключён после fatal-limit/auth сигнала.

## Важное ограничение

Это pre-run budget gate. Он выставляет env-переменные. Чтобы добиться идеального `1 token = 1 HTTP request`, внутренние provider-модули должны также проверять `*_MAX_HTTP_REQUESTS_PER_RUN` перед каждым HTTP-запросом. v12 уже передаёт такие переменные, но если конкретный provider их не поддерживает, он может частично превысить план. Поэтому следующий шаг после v12 — instrumented request wrapper внутри provider-клиентов.
