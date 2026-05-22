# sports-bot run263020 lifecycle/window truth fix

## Что исправлено

1. `scripts/build_day_inventory_coverage_truth.py`
   - Добавляет `minutes_to_kickoff` в row-level coverage truth.
   - Разделяет `strict_ready_for_publish` и фактически доступный `ready_for_publish`.
   - Исключает уже отправленные Telegram fallback-пики из `ready_for_publish` через `.data/fallback-sent-index.json` и `.data/published-candidate-index.json`.
   - Добавляет счётчики `matches_ready_for_publish_strict` и `matches_strict_ready_already_published`.

2. `scripts/publish_controlled_fallback.py`
   - После успешной fallback-публикации зеркалит выбранные ставки в стандартные артефакты:
     - `.data/exports/latest-picks.json`
     - `.data/exports/latest-bets.json`
     - `.data/exports/latest-pending-bets.json`
     - `.data/published-candidate-index.json`
   - Это нужно, чтобы bankroll/open risk и lifecycle видели fallback Telegram-публикации, а не только `.data/fallback-sent-index.json`.

3. `scripts/send_harizon_telegram_run_report_v8.py`
   - В `Current day inventory windows` показывает не только доступные ready, но и strict-ready/already-sent, чтобы не было ложного противоречия между Coverage truth и fallback.

4. `tests/test_fallback_published_artifact_and_coverage_truth.py`
   - Регресс-тест на исключение уже опубликованного матча из fresh publish-ready.
   - Регресс-тест на запись fallback-публикации в стандартные pick/bet artifacts.

## Проверка

```bash
python3 -m py_compile scripts/build_day_inventory_coverage_truth.py scripts/publish_controlled_fallback.py scripts/send_harizon_telegram_run_report_v8.py app/services/market_family_publication_guard.py app/services/runtime_startup_chain.py app/services/post_integrity_candidate_rescue.py app/services/__init__.py
PYTHONPATH=. pytest -q tests/test_fallback_published_artifact_and_coverage_truth.py
```

Локально на артефакте run263020: Fiorentina — Atalanta остаётся `strict_ready_for_publish=true`, но становится `ready_for_publish=false`, потому что уже была отправлена в Telegram.
