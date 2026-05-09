# HARIZON Sports Bot — daily prediction operating system

## Цель

Скрипт должен работать как дневной конвейер: ночью собирает полный пул матчей, днём обновляет коэффициенты и контекст, перед публикацией проверяет линию, рынок, модель, погоду, новости и риск. Контекстные API не считаются подтверждением цены: цену подтверждают только odds-источники и букмекеры.

## Текущая логика анализа

Кандидаты строятся из трёх слоёв:

```text
offers + context + market_signals
```

Уже используются:

- odds-api.io линии по двум аккаунтам;
- SStats historical form/xG-like context;
- Bzzoiro predictions/events;
- football-data standings/history;
- TheSportsDB league tables;
- ClubElo strength prior;
- market monitor: consensus, dispersion, movement, CLV;
- model logic: totals via xG/Poisson, h2h calibration, spreads, BTTS, DNB, team totals, market-derived fallback;
- quality layer and bankroll/risk guards.

## Дневной цикл

### 00:00–00:20 UTC — Daily inventory

Собрать матчи дня и ближайшего окна:

```text
odds-api.io /v3/events
football-data /v4/matches
bzzoiro /api/events/
TheSportsDB/ESPN/SportLogic/AllSports fallback
```

Сохранить:

```text
canonical_match_id, source_event_ids, teams, league, country, kickoff_utc, status, venue/city
```

### 00:20–00:45 UTC — Base context

Собрать дешёвый контекст:

```text
ClubElo date snapshot
football-data standings/teams
TheSportsDB team/league mapping
SStats /Games/list historical window
Bzzoiro predictions/events/standings when available
Football-Data.co.uk CSV cache
Wikidata aliases/venue/city cache
```

### Каждые 2 часа — Refresh cycle

Обновлять только изменяющиеся данные:

```text
odds-api.io /odds/updated or /odds/multi for missing lines
Bzzoiro /predictions/
SStats recent/today window
football-data only if stale
market monitor snapshot
```

### За 6–2 часа до матча — Shortlist enrichment

Углублять только матчи, которые могут стать ставкой:

```text
odds-api.io /odds/movements
odds-api.io точечный odds refresh
Bzzoiro odds/live if available
SStats /Games/last-games-stats
SStats /Games/glicko/{id}
Open-Meteo bulk weather
WeatherAPI final weather check
team-specific news queries
```

### За 90–20 минут до матча — Final gate

Публикация разрешена только если:

```text
bookmaker_count >= 2
odds_source_count >= 2
context_source_count >= 2 OR one elite context + strong market signal
price is not stale
line is inside market cluster
consensus dispersion is acceptable
model/xG direction does not conflict
weather/news do not block the selection
EV and edge exceed thresholds
bankroll exposure is safe
```

## Новые системы анализа

### 1. Market Integrity Engine

Проверки:

```text
bookmaker cluster
cross-account price agreement
stale price
single-book outlier
line jump
Over 1.5 absolute sanity guard
```

Для Over 1.5:

```text
if price > 1.55: require 3 exact bookmaker confirmations and block if consensus fair odds is materially lower
```

### 2. Provider Trust Score

Для каждого provider считать:

```text
match_success_rate, stale_rate, mapping_error_rate, context_hit_rate, CLV, settled ROI by provider stack
```

Использовать как множитель веса источника.

### 3. Context Ensemble Score

Считать единый ensemble:

```text
xg_home, xg_away, home/draw/away probability, over15/25/35, BTTS, disagreement_score, confidence_score
```

Весить источники:

```text
SStats detail > Bzzoiro prediction > football-data > ClubElo > TheSportsDB > self_history
```

### 4. Match Motivation Score

Добавить:

```text
league stage, title/relegation/playoff race, cup knockout, fixture congestion, rest days, travel/venue risk
```

### 5. Weather Impact Score

Добавить:

```text
wind, rain, snow/storm, temperature, pitch risk, total-goals factor
```

Сильный ветер/дождь должен блокировать слабые over-сигналы.

### 6. News and Squad Risk Score

Новости не создают ставку, а блокируют риск:

```text
injury, suspension, rotation, manager change, postponed/cancelled, must-win context
```

### 7. Line Movement Classifier

Классы:

```text
steam_with_consensus, stable_value, fake_steam_single_book, drift_against_pick, high_dispersion_noise, stale_or_missing_history
```

Публиковать только `steam_with_consensus` или `stable_value`.

### 8. Shadow Learning

Сохранять почти прошедшие ставки:

```text
candidate, rejection_reason, price, probability, provider stack, result, CLV
```

Использовать для калибровки thresholds, provider weights and market-family rules.

## API upgrade plan

### odds-api.io

Добавить:

```text
/bookmakers
/bookmakers/selected
/odds/updated
/odds/movements
```

### SStats

Слить активный provider с clean v1-логикой:

```text
/Games/list
/Games/last-games-stats
/Games/glicko/{id}
/Games/profits only diagnostic/shadow
```

### Bzzoiro

Разделить на слои:

```text
fixtures, predictions, odds, standings, live
```

### football-data

Добавить:

```text
/competitions cache
/competitions/{code}/teams
/competitions/{code}/scorers for top leagues
```

### TheSportsDB

Использовать как mapping source:

```text
searchteams.php, search_all_teams.php, lookupteam.php, eventsnext.php, eventslast.php
```

### Weather

Open-Meteo сделать bulk-primary по координатам стадионов. WeatherAPI оставить для shortlist/final verification.

## Целевая структура данных

```text
raw_provider_responses
canonical_matches
provider_event_crosswalk
team_crosswalk
league_crosswalk
offers
contexts
market_snapshots
candidate_decisions
shadow_candidates
settlements
```

## Хороший дневной результат

```text
150–300 матчей в inventory
60–80% матчей с odds
50–70% матчей с context
20–40 shortlisted матчей
3–10 high-quality candidates
1–5 publishable picks
positive CLV over time
```

No-bet день нормален, если рынок не дал качественный перевес.
