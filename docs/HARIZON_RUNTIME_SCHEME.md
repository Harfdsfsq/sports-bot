# HARIZON Runtime Scheme

Единая рабочая схема для sports-bot: бот должен каждый день собрать полный инвентарь матчей, максимально обогатить матчи линиями и контекстом, сделать акцент на ближайшие события, отфильтровать мусор и выбрать 1-5 сильнейших прогнозов дня без публикации кривых коэффициентов.

## 1. Главный принцип

Бот не должен пытаться угадать ставку из неполного хаоса данных. Правильный pipeline:

1. Собрать полный список матчей дня.
2. Дедуплицировать и нормализовать матчи.
3. Получить линии по максимуму, но без сжигания квот.
4. Выбрать priority-shortlist ближайших и ликвидных матчей.
5. Догрузить контекст только туда, где уже есть шанс на ставку.
6. Построить кандидатов.
7. Проверить целостность рынка и маппинга.
8. Выбрать топовые прогнозы дня.
9. Отправить понятный Telegram-отчёт и сохранить audit artifacts.
10. После каждого ручного прогона анализировать `latest-run-summary.md/json` и править следующий bottleneck.

## 2. Дневной цикл

### Ночной bootstrap: 00:00-01:00 локального времени

Цель: получить максимум матчей на текущий футбольный день.

Что делать:

- primary inventory: `odds_api_io` fixtures/events;
- secondary inventory: `sportlogic`, `football-data.org`, `TheSportsDB`, `API-Football` только если квота позволяет;
- сохранить day inventory в `.data/cache/day_inventory_YYYY-MM-DD.json`;
- каждому матчу присвоить стабильный `canonical_match_key`;
- не строить агрессивные прогнозы, если линии ещё пустые или старые.

Результат ночи: полный пул матчей дня + первичная карта источников.

### Дневные run каждые 2 часа

Цель: обновлять линии, добирать контекст, ловить value до старта матчей.

Приоритет:

1. Матчи в ближайшие 0-12 часов.
2. Матчи с уже найденными линиями.
3. Матчи из сильных/ликвидных лиг.
4. Матчи, где есть 2+ independent odds/context sources.
5. Остаток инвентаря — только если есть свободная квота.

### Последний run перед вечерними матчами

Цель: выбрать финальный shortlist топовых прогнозов дня.

Правило: лучше 0-2 сильных прогноза, чем 5 слабых. Запрещено ослаблять quality thresholds, пока не доказано, что bottleneck в matching/parser, а не в реальном отсутствии value.

## 3. Слои данных

### Layer A — inventory

Источники:

- `odds_api_io` — основной источник матчей и линий;
- `sportlogic` — fallback/probe inventory + context;
- `football-data.org` — fixture/result/standings для популярных лиг;
- `TheSportsDB` — справочник команд/лиг/событий;
- `API-Football/API-Sports` — дорогой по дневной квоте источник, использовать точечно;
- `ClubElo` и `Football-Data.co.uk` — кэшируемые CSV/HTTP источники, обновлять редко.

Inventory match object должен хранить:

```json
{
  "canonical_match_key": "date|league|home|away",
  "kickoff_utc": "...",
  "league": "...",
  "country": "...",
  "home_team": "...",
  "away_team": "...",
  "source_ids": {
    "odds_api_io": "...",
    "sportlogic": "...",
    "football_data": "..."
  },
  "aliases": {
    "home": ["..."],
    "away": ["..."]
  }
}
```

### Layer B — odds

Каждая линия должна храниться raw + normalized:

```json
{
  "match_key": "...",
  "source": "odds_api_io",
  "bookmaker": "Bet365",
  "market_family": "totals",
  "market_key": "match_total",
  "selection": "over",
  "point": 2.5,
  "odds": 1.91,
  "pulled_at_utc": "...",
  "raw_market_name": "...",
  "raw_selection_name": "..."
}
```

Обязательные guards:

- `Over 1.5` выше `1.85` — hard reject, пока не подтверждено 3+ точными букмекерами;
- single-source odds не публиковать, если нет 3+ букмекеров внутри source;
- quarter totals разрешены только если правильно распарсены paired lines;
- handicaps/spreads не публиковать до полного парсера пар `home/away`;
- team totals не публиковать до отдельной проверки mapping.

### Layer C — context

Контекст не должен тратиться на все матчи подряд. Сначала линии, потом context shortlist.

Контекстные источники:

- SStats/Bzzoiro — если дают прогнозные/статистические сигналы;
- API-Football — standings, form, injuries, lineups, H2H, stats, но с дневным лимитом;
- football-data.org — standings/schedule/results;
- TheSportsDB — aliases/team metadata;
- Open-Meteo/WeatherAPI/OpenWeatherMap — погода по стадиону/городу;
- NewsAPI/GNews/Guardian/NewsData/Currents — только для важных матчей и новостного риска;
- ClubElo — сила команд;
- Football-Data.co.uk — historical odds/results.

Context object должен быть пригоден для explainability:

```json
{
  "match_key": "...",
  "signals": {
    "elo_delta": 43,
    "form_home": "W-D-W-L-W",
    "injury_risk": "medium",
    "weather_risk": "low",
    "news_risk": "none"
  },
  "source_count": 4,
  "source_quality": 0.78,
  "notes": ["..."]
}
```

## 4. Правильный порядок запросов к API

### Step 1 — cheap cache-first sources

- ClubElo: 1 раз в день.
- Football-Data.co.uk: 1 раз в день или реже.
- TheSportsDB aliases: кэшировать надолго.
- Wikidata aliases: только missing aliases, очень редко.

### Step 2 — primary inventory + odds

- odds-api.io: получить события/линии пачками по sport/league/date.
- Не делать запрос на каждый матч, если API позволяет получить пачку.
- Сохранять raw response для parser debug.
- Считать actual request count и effective match yield.

### Step 3 — fallback inventory

Использовать только когда primary inventory дал подозрительно мало матчей или матч без линии важный:

- sportlogic games/fixtures;
- football-data.org matches;
- TheSportsDB events;
- API-Football fixtures.

### Step 4 — targeted context

Тратить квоты по shortlist:

1. Матчи с odds и kickoff < 12h.
2. Матчи с high market signal или value pre-score.
3. Матчи с 2+ odds sources.
4. Матчи без enough context, но с хорошей линией.

### Step 5 — news/weather only where relevant

- Weather: только outdoor football и когда город/venue известен.
- News: только топовые/важные матчи, максимум 1-2 запроса на матч, кэшировать.

## 5. Бюджеты бесплатных API

Использовать 70-80% от публичного лимита, потому что reset/timezone/provider dashboard могут отличаться.

| Provider | Free limit / safe planning | Runtime role |
| --- | --- | --- |
| odds-api.io | спорный лимит: планировать консервативно 100 req/hour до проверки dashboard | primary odds + inventory |
| API-Football/API-Sports | 100 req/day | точечный context, не массовый scan |
| SportLogic | 500 req/day, 10 req/min | fallback fixtures/context/probe |
| football-data.org | 10 req/min registered | fixtures/standings cache |
| TheSportsDB | 30 req/min | aliases/reference/context |
| AllSportsAPI | 260 req/hour, но free покрытие лиг ограничено | fallback/probe |
| SStats | точный лимит не подтверждён | использовать по observed quota |
| Bzzoiro | по observed quota | context/prediction signal |
| FutrixMetrics | 300 req/hour, 30 RPM | player/team signal, но only shortlist |
| WeatherAPI | 100k/month | weather fallback |
| OpenWeatherMap | 60/min, product-specific daily/monthly limits | weather fallback |
| Open-Meteo | fair use, no key | primary weather |
| NewsAPI | 100/day, dev limitations | news fallback |
| GNews | 100/day | news fallback |
| NewsData.io | 200 credits/day | news fallback |
| Guardian | 500/day, 1/sec | quality news fallback |
| ClubElo | no published quota | daily cached Elo |
| Football-Data.co.uk | no API quota | daily/weekly CSV cache |

## 6. Matching strategy

### Canonicalization

Для каждой команды хранить:

- normalized name;
- aliases from providers;
- country;
- league;
- optional Wikidata/TheSportsDB ids;
- historical provider ids.

Normalization:

- lower-case;
- убрать `fc`, `cf`, `afc`, `sc`, `club`, точки, лишние пробелы;
- заменить unicode accents;
- учесть common aliases: `Man United`, `Manchester Utd`, `Manchester United FC`;
- не матчить только по имени, если league/country конфликтуют.

### Match score

Матч считается совпавшим, если:

- kickoff delta <= 6 часов для pre-match, лучше <= 2 часа;
- home similarity >= 0.86;
- away similarity >= 0.86;
- league/country not conflicting;
- если teams swapped — явно пометить и проверить market side.

Score:

```text
0.40 home_team_similarity
0.40 away_team_similarity
0.10 kickoff_closeness
0.10 league/country compatibility
```

Reject:

- same teams but wrong date;
- wrong competition;
- U21/reserve/women mismatch;
- home/away swapped без явного исправления odds side.

## 7. Candidate selection

### Raw candidate

Создавать candidate только если есть:

- valid market family;
- normalized odds;
- market probability;
- model probability или controlled fallback probability;
- no market parser warning;
- no stale odds.

### Quality gates

Публиковать только если:

- 2+ lines/sources или 1 source + 3+ bookmakers;
- edge не ниже final threshold;
- EV положительный;
- confidence достаточный;
- нет конфликта с xG/context;
- market integrity passed;
- не повтор уже опубликованной ставки;
- stake > 0 и risk budget позволяет.

### Selection rank

Сортировка:

1. `publication_score`;
2. `edge_pct`;
3. `ev_pct`;
4. source/bookmaker count;
5. liquidity/league quality;
6. kickoff proximity.

Ограничения:

- максимум 1 ставка на матч;
- максимум 2 ставки за run;
- дневной максимум регулируется bankroll/risk;
- лучше не публиковать, чем публиковать low-confidence мусор.

## 8. Telegram report

Каждый run должен отправлять/сохранять:

- сколько матчей собрано;
- сколько с линиями;
- сколько с контекстом;
- сколько raw candidates;
- сколько passed quality;
- сколько publishable;
- почему не прошли;
- provider audit;
- selected picks or no-pick reason.

Для каждой ставки:

- матч, лига, kickoff;
- рынок, selection, line, odds;
- source count, bookmaker count;
- market probability, model probability, adjusted probability;
- edge, EV, confidence;
- короткое объяснение;
- risk/stake;
- integrity notes.

## 9. После каждого ручного прогона

После workflow/manual run нужно смотреть:

1. `.data/exports/latest-harizon-telegram-run-report.txt`
2. `.data/exports/latest-run-summary.md`
3. `.data/exports/latest-run-summary.json`
4. `.data/exports/latest-controlled-fallback-report.json`
5. `.data/exports/latest-candidate-integrity.json`
6. `.data/exports/latest-run-bot.log`

В чат отправлять:

- Telegram сообщение/отчёт;
- `latest-run-summary.md`;
- если есть ошибка — хвост `latest-run-bot.log`;
- если есть подозрительная ставка — candidate snapshot из `latest-run-summary.json`.

## 10. Итерационная разработка

Каждая новая правка должна бить один конкретный bottleneck:

- мало матчей → inventory/bootstrap;
- много матчей, мало линий → odds запросы/bookmaker aliases/pagination;
- есть линии, нет candidates → parser/CandidateFactory;
- candidates есть, всё режется → quality reasons;
- есть publishable, но Telegram пустой → publication/stake/seen/open risk;
- кривые коэффициенты → market parser + exact-line integrity;
- provider даёт data, но matched=0 → team/league/date matching.

Запрещено:

- просто снижать thresholds ради публикаций;
- публиковать single-source odds без жёсткой проверки;
- смешивать API-Football и API-Sports как независимые источники, если backend один;
- считать news/context source подтверждением цены;
- тратить daily-limited API на весь inventory без shortlist.

## 11. Целевое состояние

Хороший run выглядит так:

```text
matches_seen: 200-800
matches_with_offers: 55-80% от inventory
contexts_built: 60-90% от матчей с линиями в priority window
raw_candidates: >0 почти каждый дневной run
quality_pass_rate: 5-25%
publishable: 0-3 за run
published/day: 1-5, только если value реально есть
market_anomalies: 0 hard_guard
provider_yield_issues: понятны и уменьшаются от итерации к итерации
```

Главный KPI не количество ставок, а отсутствие мусорных публикаций и стабильный рост качества: coverage, matching rate, source count, bookmaker count, CLV, ROI по settled bets.
