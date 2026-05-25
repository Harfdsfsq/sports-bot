# RUN263966 accumulation analytics v8

Патч переводит фильтр `api_coverage-only` из отдельного repair-step прямо в основные скрипты накопления.

## Что исправляет

- `prediction-ledger.jsonl` больше не получает строки, которые пришли только из `latest-api-coverage-consensus-runtime-patch.json`.
- Такие строки остаются в opportunity/calibration diagnostics как `opportunity_only`, но не считаются прогнозами.
- Если в текущем run уже была записана coverage-only строка, `update_prediction_ledger.py` удалит её при повторном запуске для этого же `GITHUB_RUN_ID`.
- `latest-prediction-ledger-summary.json` должен показывать `rows_missing_core_metrics_current_run: 0`, если все реальные prediction rows имеют identity + odds + EV + edge.

## Проверка на run-bot-26396684482

- До: `current_run_rows: 5`, `rows_missing_core_metrics_current_run: 1`.
- После: `current_run_rows: 4`, `rows_missing_core_metrics_current_run: 0`.
- `latest-prediction-calibration-audit.json`: `candidate_keys: 4`, `coverage_only_rows_excluded: 1`.

Публикационные guards не меняются.
