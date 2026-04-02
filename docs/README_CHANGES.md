# Что изменено

Основные правки в этом наборе:

1. **Вернул логику выгрузки в Google Sheets**
   - Python-бот теперь пишет `.data/sheet-export.json` и `.data/sheet-export.csv`.
   - Добавлен `apps_script/sheet_sync.gs`, который забирает этот JSON из GitHub и записывает его в Google Sheets через Apps Script, как в старой версии.

2. **Подключён Bzzoiro как дополнительный источник контекста**
   - Используется `BZZOIRO_API_KEY`.
   - Берутся upcoming events и ML predictions.
   - Контекст даёт win probabilities, confidence и приближение expected goals.

3. **Подключён API-Football как дополнительный fallback-контекст**
   - Используется `API_FOOTBALL_KEY`.
   - Берутся fixtures и predictions по fixture id.
   - Это помогает поднять качество контекста там, где SStats и Bzzoiro пустые.

4. **Переписан провайдер Odds-API.io**
   - Используется правильный query-параметр `apiKey`.
   - Сначала загружаются events, потом они матчятся с локальными матчами, затем только по совпавшим event ids запрашиваются odds через `/v3/odds/multi`.

5. **Отсечены симуляции / eSoccer / виртуальные события**
   - Фильтр добавлен в bootstrap, BookiesAPI и Odds-API.io.
   - Отсекаются события типа `Liverpool (pikalicaaa)` / `Germany (Profik)` и похожие synthetic feeds.

6. **Смягчён фильтр по букмекерам**
   - Если нет ни одного оффера из `TARGET_BOOKMAKERS`, бот пробует fallback на `CONSENSUS_BOOKMAKERS`.
   - Это снижает количество пустых прогонов при наличии нормальной рыночной цены у sharp books.

7. **Workflow теперь реально читает все нужные secrets/vars**
   - Добавлены `BZZOIRO_API_KEY`, `API_FOOTBALL_KEY`, `SHEET_ID`, `ALLOW_LOW_TIER`, `MIN_*`, `MAX_MATCHES_FOR_ODDS_FETCH`, `BOOKIES_API_ODDS_FETCH_LIMIT` и совместимость `TELEGRAM_TOKEN` -> `TELEGRAM_BOT_TOKEN`.

---

## Как включить авто-запись в Google Sheets

### Вариант без новых Google credentials
1. Оставляешь GitHub Action как есть.
2. Он будет коммитить `.data/sheet-export.json` и `.data/sheet-export.csv`.
3. В Apps Script вставляешь `apps_script/sheet_sync.gs`.
4. В Script Properties задаёшь:
   - `SHEET_ID`
   - `SHEET_NAME` = `ValueBets`
   - `RAW_JSON_URL` = `https://raw.githubusercontent.com/Harfdsfsq/sports-bot/main/.data/sheet-export.json`
5. Запускаешь `create15MinTrigger()` один раз.

### Почему не сделан прямой Python -> Google Sheets write
В текущем наборе secrets нет сервисного аккаунта Google (`credentials.json` / JSON secret). Поэтому прямой серверный write из GitHub Actions в Sheets без дополнительной авторизации работать не будет.

