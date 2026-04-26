# Auto-learning and unified runtime structure

## Цель

Скрипт должен работать как единый конвейер:

```text
profile/env
→ quota governor
→ data collection
→ model/quality layer
→ controlled fallback publisher
→ audits/training dataset
→ auto-learning feedback loop
→ detailed Telegram diagnostics
→ artifacts
→ safe persistent state sync
```

## Что делает автообучение

Новый модуль:

```bash
python scripts/auto_learning_engine.py
```

читает:

```text
.data/state.json
.data/exports/latest-training-dataset.json
artifacts/controlled-fallback-report.json
.data/exports/latest-profit-distance-*.json
```

и пишет:

```text
.data/learning-state.json
.data/auto_learning_runtime_overrides.env
.data/calibration-profile.json
.data/exports/latest-auto-learning-report.json
.data/exports/latest-auto-learning-report.txt
```

## Безопасность

Автообучение не ослабляет guard’ы автоматически.

По умолчанию оно может только:

```text
• наблюдать;
• фиксировать calibration bias;
• выделять слабые bucket’ы;
• ужесточать proxy/single-source thresholds после достаточной плохой статистики.
```

Ослабление запрещено:

```json
"allow_threshold_relief": false
```

## Почему так

На ставках опасно “учиться” по маленькой выборке. Поэтому minimum:

```json
"min_settled_total": 30,
"min_bucket_samples": 12
```

Пока данных меньше, скрипт пишет:

```text
observe_only
```

и не меняет thresholds.

## Как применяется обучение

В начале следующего run workflow читает:

```text
.data/auto_learning_runtime_overrides.env
```

Затем обычные runtime env применяются поверх/вместе с ним.

## Структура workflow

### run-bot

```text
1. healthcheck
2. prepare folders
3. apply profile
4. apply quota profile
5. apply final overrides
6. apply auto-learning overrides
7. run bot once
8. controlled fallback
9. ledgers/coverage/probes
10. audits + training dataset
11. auto-learning
12. detailed run report
13. artifact upload
14. safe persistent state sync
```

### daily-report

```text
1. settlement
2. audits + training dataset
3. auto-learning
4. Russian daily operations report
5. artifact upload
6. safe persistent state sync
```

## Ручная проверка

```bash
python scripts/runtime_healthcheck.py
python scripts/auto_learning_engine.py
cat .data/exports/latest-auto-learning-report.txt
```


## v2 adjustments

- Telegram detailed report now shows up to 5 near-miss candidates by default to avoid awkward message splitting.
- If Telegram still needs multiple messages, each part is labeled `часть N/M`.
- Auto-learning no longer displays one-loss ROI as a decision signal when sample is below the configured minimum.
- Near-miss reasons in the auto-learning report are translated to Russian.
- Added translations for `tier_a_odds_above_max` and related odds/tier reasons.


## v3: защита от дублей подробного отчёта

Файл:

```text
.data/detailed-run-report-sent.json
```

теперь сохраняется как persistent state. Поэтому cooldown/hash detailed report работает между разными GitHub runs, а не только внутри одного запуска.

Persistent state теперь включает:

```text
.data/detailed-run-report-sent.json
.data/daily-ops-report-sent.json
.data/learning-state.json
.data/auto_learning_runtime_overrides.env
```

Это убирает повторные одинаковые no-pick отчёты, если два run’а проходят рядом.


## v4 notes

- `workflow_dispatch` now sends detailed no-pick diagnostics to Telegram.
- `push` still does not send no-pick diagnostics.
- `detailed-run-report-sent.json` is persistent, so cooldown works across separate GitHub Actions runs.
- Added alias cleanup for bad Cyrillic transliterations such as:
  - `Дунедин Кити Роиалс ФК` → `Данидин Сити Ройалс`
  - `Ферримеад Баис` → `Ферримид Бэйс`
  - `ФК Хаугесунд 2` → `Хаугесунд 2`
  - `Стабаек Фотбалл 2` → `Стабек 2`
