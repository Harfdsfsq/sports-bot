# sports-bot run 262909 follow-up patch

## Почему патч нужен

В запуске `run-bot-26290903265` fallback увидел 2 reserve-кандидата, но оба имели отрицательную canonical value после пересчёта. Эти кандидаты появились из controlled-consensus rescue: слой строил резервные строки по paired consensus, даже если выбранный коэффициент уже хуже fair probability по консенсусу.

Также отчёт показывал SportLogic как active core line provider, хотя quota contract дал `sportlogic: 0`.

## Что меняется

1. `app/services/controlled_rescue_consensus_guard_patch.py`
   - блокирует rescue-кандидата до качества, если `consensus_probability - selected_implied_probability < 0`;
   - пишет `.data/exports/latest-controlled-rescue-consensus-guard.json`.

2. `app/services/progressive_active_core_budget_patch.py`
   - после записи progressive plan исключает provider из active core, если в per-run contract у него grant `0` или provider disabled;
   - пишет `.data/exports/latest-progressive-active-core-budget-patch.json`.

3. `app/services/__init__.py`
   - устанавливает оба патча автоматически.

4. Тесты:
   - `tests/test_controlled_rescue_consensus_guard_patch.py`
   - `tests/test_progressive_active_core_budget_patch.py`

## Важно

Патч не ослабляет публикацию. Он только убирает отрицательные market-consensus rescue-кандидаты раньше и исправляет misleading core coverage report.
