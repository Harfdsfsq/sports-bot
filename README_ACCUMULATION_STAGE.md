# HARIZON accumulation-stage patch

Цель патча — перевести бота из режима точечных исправлений в режим накопления и анализа прогнозов.

Что включено:

1. `run-bot` вызывает `send_harizon_telegram_run_report_v9.py` перед v8.
2. `candidate_factory_output_dedup_patch` подключён последним CandidateFactory-wrapper'ом.
3. Добавлен audit `latest-candidate-opportunity-audit.json`: связывает coverage-ready матчи и реальные candidate opportunities.
4. Добавлен audit `latest-prediction-calibration-audit.json`: показывает EV/edge до и после quality/API calibration.
5. Добавлен forward-test ledger `.data/prediction-ledger.jsonl` и summary `latest-prediction-ledger-summary.json`.
6. Добавлен safe annotation patch `candidate_inventory_evidence_annotation_patch.py`, который прикрепляет inventory evidence к кандидатам без ослабления guards.

Публикационные правила не ослаблены:
- SStats остаётся контекстом, не линией.
- Tier A/B всё ещё требуют independent odds sources.
- negative EV/edge, xG conflict, low quality и Tier C watch-only блокируются.

После следующего прогона проверять в первую очередь:
- `.data/exports/latest-harizon-telegram-run-report-v9-status.json`
- `.data/exports/latest-candidate-factory-output-dedup.json`
- `.data/exports/latest-candidate-opportunity-audit.json`
- `.data/exports/latest-prediction-calibration-audit.json`
- `.data/exports/latest-prediction-ledger-summary.json`
- `.data/prediction-ledger.jsonl`
