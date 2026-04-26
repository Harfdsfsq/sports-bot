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
