# run263740 v9 + dedup follow-up

Что исправляет:
- `debug_candidates_before_quality: 3` больше не считается pre-evaluation filter, если fallback реально `seen/evaluated > 0`.
- Новый v9 renderer показывает source-pool отдельно от reject filters.
- CandidateFactory output dedup убирает точные дубли одного и того же матча/рынка/выбора/линии до diagnostics/fallback.
- Workflow должен запускать v9 первым, затем v8/v7 как fallback.

Файлы:
- app/services/candidate_factory_output_dedup_patch.py
- scripts/send_harizon_telegram_run_report_v9.py
- tests/test_candidate_dedup_and_report_v9_followup.py
- run263740-v9-dedup-followup.patch

Применение:
1. Скопировать новые файлы в репозиторий.
2. Применить patch `run263740-v9-dedup-followup.patch`.
3. Запустить:
   python -m py_compile scripts/send_harizon_telegram_run_report_v9.py app/services/candidate_factory_output_dedup_patch.py
   PYTHONPATH=. python -m pytest -q tests/test_candidate_dedup_and_report_v9_followup.py
