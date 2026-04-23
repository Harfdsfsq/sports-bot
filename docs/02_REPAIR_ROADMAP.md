# Roadmap ремонта

## Фаза 1 — Стабилизация инфраструктуры
### Цель
Один стабильный прогон, один bundle, воспроизводимый анализ.

### Задачи
- оставить один fixed-run workflow
- безопасно загружать env в GitHub Actions
- писать latest/fixed-run артефакты в стабильные пути
- проверить secrets и наличие odds API key

### Критерий готовности
- workflow стабильно заканчивается
- `fixed-run-bundle.zip` собирается каждый раз
- нет падений из-за env-файлов

## Фаза 2 — Canonical candidate layer
### Цель
Один канонический набор полей кандидата.

### Что внедрить
- `selected_odds`
- `selected_implied_probability`
- `market_probability`
- `fair_odds_from_market`
- `adjusted_probability` как единственный canonical probability
- `final_probability` только alias canonical probability

### Критерий готовности
- нет рассинхрона `odds ↔ implied`
- нет рассинхрона `adjusted ↔ source_summary.adjusted`
- `ev_pct` и `edge_pct` считаются из одного probability-source

## Фаза 3 — Hard integrity reject
### Цель
Не пускать грязные кандидаты дальше pipeline.

### Что внедрить
- reject до quality
- отдельные коды причин:
  - `reject_odds_implied_mismatch`
  - `reject_adjusted_probability_mismatch`
  - `reject_negative_edge_positive_ev`
  - `reject_market_fair_odds_mismatch`

### Критерий готовности
- suspicious candidates → 0 в main publish path

## Фаза 4 — Main publish policy
### Цель
Main-канал получает только clean signals.

### Что внедрить
- fallback → только shadow
- single-source → не main
- `quality_status != passed_quality` → не main
- `quality_last_resort`, `historical_guard_relief`, `emergency_publish` → не main

### Критерий готовности
- main publish report не содержит fallback reason
- в main-канале нет single-source и last_resort сигналов

## Фаза 5 — Strategy hardening
### Цель
Сузить боевой пул рынков и снизить дисперсию.

### Main publish рынки
- totals
- dnb / AH0

### Shadow only
- btts
- h2h high odds
- spreads
- non-core
- cup matches
- single-source

### Диапазон коэффициентов
- основной боевой: 1.75–2.35
- всё выше → отдельная проверка или shadow

### Критерий готовности
- средний боевой коэффициент снижается
- candidate quality растёт
- main publish становится реже, но чище

## Фаза 6 — Tests + Monitoring
### Цель
Раннее обнаружение регрессий.

### Что внедрить
- unit tests на canonical fields
- integration tests на publish gate
- alert на suspicious ratio
- alert на падение ROI и рост avg odds
- alert на empty-run due to outside window
