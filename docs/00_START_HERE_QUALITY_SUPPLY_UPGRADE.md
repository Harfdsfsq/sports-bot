# Quality + supply upgrade fix

## Что исправляет

Пакет повышает качество контролируемого резерва и сохраняет широкое наполнение свежими кандидатами.

### 1. Proxy-quality больше не даёт Tier A

`quality_score_source=proxy` теперь используется только для резервного ранжирования. Уровень A запрещён без настоящего quality-layer.

### 2. xG sanity guard для тоталов

Для `totals` и `teamTotals` резерв считает независимую вероятность через poisson от `expected_home + expected_away`.

В `controlled-fallback-report.json` появятся поля:

- `xg_probability_pct`
- `xg_model_gap_pp`
- `xg_abs_gap_pp`
- `xg_direction_ok`

### 3. Тоталы с сильным расхождением режутся или понижаются

Пороги:

```env
CONTROLLED_FALLBACK_TIER_A_MAX_XG_GAP_PP=6.5
CONTROLLED_FALLBACK_TIER_B_MAX_XG_GAP_PP=10.0
CONTROLLED_FALLBACK_TIER_C_MAX_XG_GAP_PP=13.0
CONTROLLED_FALLBACK_XG_HARD_REJECT_GAP_PP=14.0
```

### 4. Наполнение кандидатов расширено

```env
MAX_INTERNAL_CANDIDATES_PER_RUN=48
MAX_CANDIDATES_PER_MATCH_PRE_FILTER=6
MAX_RESCUE_CANDIDATES_PER_MATCH=12
MAX_RESCUE_PREFILTER_CANDIDATES_EXPORT=400
CONTROLLED_FALLBACK_INCLUDE_STATE_SHADOW=false
```

Старые shadow-кандидаты не возвращаются как источник ставок.

### 5. В Telegram добавляется xG-проверка

Пример:

```text
🔎 xG-проверка: ориентир 50.1% | разрыв +9.1 п.п.
```

## Как применить

1. Распакуй архив в корень репозитория.
2. Проверь diff в GitHub Desktop.
3. Commit + push.
4. Запусти обычный `Run bot` с profile `balanced`.
5. Пришли `run-bot-current`.
