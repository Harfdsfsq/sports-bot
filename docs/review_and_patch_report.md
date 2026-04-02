# Оценка sports-bot и сделанные правки

## Что было не так по логам

1. **Главный bottleneck — матчинг событий между источниками**, а не сам расчёт вероятностей.
   - `matches_seen`: 935
   - `matches_with_offers`: 7
   - `odds_api_io.events_fetched`: 240, `events_matched`: 0
   - `bookies_api.events_fetched`: 200, `events_matched`: 8

2. **Бот публиковал `market_only` прогноз** без независимого контекстного подтверждения.
   В примере из Telegram:
   - `mode=market_only`
   - `model=consensus`
   - `market_prob=model_prob`

3. **В коде был критический баг нормализации названий команд**:
   замена `"st " -> "saint "` портила слова `West`, `Stoke` и т.п.
   Из-за этого матчинг вроде `Вест Бромвич -> West Bromwich` ломался.

4. **Низкие лиги не фильтровались в bootstrap-источнике**, хотя в конфиге уже есть `ALLOW_LOW_TIER`.
   Поэтому, когда primary source переключался на `bookies_bootstrap`, бот начинал работать по очень шумным лигам.

5. **`publish_window_hours` был в конфиге, но фактически не использовался.**

6. **SStats-контекст использовался не полностью**:
   `home_win_probability`, `away_win_probability` и реальная уверенность контекста не прокидывались в модель, хотя структура это поддерживает.

## Что я изменил

### 1) Улучшил матчинг команд и событий
Файлы:
- `app/utils.py`

Сделано:
- исправлен баг с `st -> saint`
- добавлена более устойчивая phonetic/string similarity логика
- улучшен `score_event_match()` для сопоставления русской/латинской записи команд

### 2) Улучшил SStats-контекст
Файлы:
- `app/providers/sstats.py`

Сделано:
- контекст теперь сохраняет `home_win_probability` и `away_win_probability`
- confidence контекста теперь реально рассчитывается
- если на один матч нашлось несколько строк, остаётся лучшая, а не случайно последняя

### 3) Отфильтровал низкие лиги на bootstrap-источнике
Файлы:
- `app/providers/bookies_bootstrap.py`

Сделано:
- `ALLOW_LOW_TIER=false` теперь реально влияет и на bootstrap-source
- матчам присваивается tier (`low` / `mid`)
- нормализация названий команд и лиг переведена на общую боевую логику

### 4) Улучшил приоритизацию матчей для BookiesAPI
Файлы:
- `app/providers/bookies_api.py`

Сделано:
- матчи теперь сортируются по приоритету, а не просто по времени
- приоритет выше у:
  - матчей внутри publish window
  - не low-tier матчей
  - матчей с `bet365_id`
- low-tier матчи отсекаются, если `ALLOW_LOW_TIER=false`

### 5) Начал реально использовать publish window
Файлы:
- `app/services/runner.py`

Сделано:
- перед построением офферов и контекста матчи фильтруются по `publish_window_hours`
- в summary добавлено `matches_before_publish_window`

### 6) Ужесточил отбор слабых market-only ставок
Файлы:
- `app/services/model.py`

Сделано:
- confidence снижается для low-tier и чисто market-only сценариев
- publication score теперь сильнее предпочитает model-backed ставки над `market_only`
- слабые `market_only` ставки в low-tier лигах режутся жёстче

## Локальная проверка после патча

Сравнение старого и нового матчинга на типовых кейсах:

- `Вест Бромвич / Рексем` -> `West Bromwich / Wrexham`
  - было: `0.0 / no match`
  - стало: `117.07 / fuzzy`

- `Сток / Шеффилд Уэнсдей` -> `Stoke / Sheffield Wednesday`
  - было: `0.0 / no match`
  - стало: `115.55 / fuzzy`

- `Матаре Юнайтед / Kenya Police FC` -> `Mathare United / Kenya Police`
  - было: `76.4`
  - стало: `122.4`

- `КФ Башкими / Тиквес Кавадарчи` -> `KF Bashkimi / Tikves Kavadarci`
  - было: `89.73`
  - стало: `120.95`

## Что ещё обязательно поменять в ENV/Secrets

По логу видно:
- `bookies_api.candidate_matches_limited_to = 10`

Это **очень мало**. Даже после кода бот будет искусственно смотреть только на 10 матчей.
Рекомендация:
- поставить `MAX_MATCHES_FOR_ODDS_FETCH=50` для экономного режима
- или `100-150`, если API-квота позволяет

## Что я ожидаю после этих правок

1. Существенно вырастет число сматченных событий между bootstrap / odds-api / sstats.
2. У бота станет меньше `market_only`-публикаций из низких лиг.
3. Вероятности начнут чаще опираться на независимый контекст (`xG`, win probabilities), а не только на market consensus.
4. Telegram-выдача станет более узкой, но качественнее по сигналу.
