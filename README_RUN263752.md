# run263752 v9 + dedup final wiring fix

## Почему был снова v8

В свежем запуске прогнозная логика отработала корректно: Paderborn и Columbus были проверены fallback, но не опубликованы:
- Paderborn: `telegram publish odds sources guard:1/2`, `Tier C watch-only`;
- Columbus Crew 2: xG conflict;
- публикация заблокирована правильно.

Инфраструктурная проблема осталась прежней: workflow всё ещё запускал v8 напрямую, а `candidate_factory_output_dedup_patch` не был последним runtime-wrapper’ом в `runtime_startup_chain.py`.

## Что делает патч

1. Включает `send_harizon_telegram_run_report_v9.py` перед v8 в `.github/workflows/run-bot.yml`.
2. Подключает `candidate_factory_output_dedup_patch` последним в `runtime_startup_chain.py`.
3. Улучшает v9: если fallback реально `seen/evaluated > 0`, отчёт больше не говорит, что fallback не оценивал кандидатов.
4. Source counters (`debug_candidates_before_quality`) показываются как источники пула, а не как причины отказа.
5. Mixed-case поддержан: если часть кандидатов оценена, а часть отфильтрована до fallback, v9 пишет это отдельно.

## Проверка

```bash
python -m py_compile scripts/send_harizon_telegram_run_report_v9.py app/services/candidate_factory_output_dedup_patch.py
PYTHONPATH=. python -m pytest -q tests/test_report_v9_pool_and_dedup_final.py
```

Публикационные правила не ослаблены.
