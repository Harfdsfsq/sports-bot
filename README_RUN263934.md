# RUN 263934 — accumulation analytics v4

## Что исправлено

После run `26393444415` стадия накопления почти нормальная:

- `v9` работает.
- `run-once` полный.
- fallback оценил 3 кандидата.
- ledger пополнился 4 строками.
- calibration audit создан.

Оставался только один ложный аналитический дефект:

`rows_missing_core_metrics_current_run: 1`

Причина: кандидат `FC RFS — FK Tukums 2000/TSS` дошёл только через sparse API/value/quality artifacts и не имел `quality`, хотя у него были ключевые поля для forward-test:

- команды;
- рынок/выбор/линия;
- odds;
- EV;
- edge;
- причины отказа.

`quality` полезен, но не всегда экспортируется для кандидатов, которые не дошли до fallback. Поэтому считать отсутствие `quality` ошибкой core metrics неправильно.

## Файлы

- `scripts/update_prediction_ledger.py`
- `scripts/build_prediction_calibration_audit.py`
- `tests/test_accumulation_analytics_v4.py`

## Ожидаемый результат следующего run

- `latest-prediction-ledger-summary.json`
  - `rows_missing_core_metrics_current_run: 0`
- `latest-prediction-calibration-audit.json`
  - `rows_missing_core_metrics: 0`
  - `rows_with_quality` показывает отдельный счётчик качества.
- Sparse rows без quality остаются в ledger, но не считаются сломанными, если есть identity + odds + EV + edge.

## Важно

Публикационные правила не изменены. Это только аналитика/forward-test ledger.
