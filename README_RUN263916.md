# run263916 accumulation analytics v2

Исправляет последнюю найденную проблему стадии накопления:

- `latest-prediction-ledger-summary.json` уже создавался, но `rows_missing_core_metrics` считался по всей старой истории и выглядел как текущая ошибка.
- `latest-prediction-calibration-audit.json` иногда терял `home_team/away_team/odds/quality`, потому что sparse quality/API row перекрывал rich fallback row.

Изменения:
- `scripts/build_prediction_calibration_audit.py`
  - сливает before/value/api/quality/fallback rows в один logical candidate row;
  - читает nested `metrics`, `source_summary`, `diagnostics`;
  - сохраняет причины отказа из всех стадий;
  - добавляет `rows_missing_core_metrics` в counts.
- `scripts/update_prediction_ledger.py`
  - читает nested metrics из fallback/API/quality;
  - считает `rows_missing_core_metrics_current_run` отдельно от total;
  - не пишет runtime_error по нефатальным warning, если fallback artifact существует.
- `tests/test_accumulation_analytics_v2.py`

Проверка:
```bash
python -m py_compile scripts/build_prediction_calibration_audit.py scripts/update_prediction_ledger.py
PYTHONPATH=. pytest -q tests/test_accumulation_analytics_v2.py
# 3 passed
```

Публикационные правила не изменены.
