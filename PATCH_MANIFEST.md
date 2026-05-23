# sports-bot fast balanced follow-up

Исправляет слишком агрессивный fast-run, где рынок был урезан до `odds req 4`, `2+ books 0`, `Bzzoiro secondary 0`.

## Файлы

- `.github/workflows/run-bot-fast.yml`
  - balanced fast defaults;
  - re-apply fast budgets after quota contract;
  - гарантирует controlled-fallback artifact;
  - пишет fast-depth diagnostic.

- `scripts/apply_fast_run_budget.py`
  - сохраняет минимум рыночной глубины: odds-api.io budget 120, odds match target 220, Bzzoiro odds backfill включён;
  - отключает SportLogic в fast только если нет свежих сигналов или он явно не включён.

- `scripts/ensure_controlled_fallback_report.py`
  - создаёт no-op fallback artifact, если fallback step не записал отчёт.

- `scripts/assert_fast_run_depth.py`
  - warning-only диагностика, если fast-mode снова дал слишком мало линий/2+ books/Bzzoiro overlap.

- `tests/test_fast_run_balanced_depth.py`
  - регресс-тесты на balanced fast budgets и workflow order.

## Что НЕ меняется

- Tier A/B publication guards не ослаблены.
- SStats остаётся context-only.
- 2 independent odds sources для Tier A остаются обязательными.
- EV/edge/xG/quality guards не трогаются.
