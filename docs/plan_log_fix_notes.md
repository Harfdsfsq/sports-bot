# Plan + log follow-up patch

Этот патч собран по трем источникам:
- план улучшения: offline-отчётность, training dataset, SQLite warehouse, coverage audit;
- последний run-log: `self_history_contexts_built = 0`, `publishable_with_derived_market_signal = 0`, заметная single-source зависимость;
- текущий код бота после предыдущих фиксов.

## Что изменено

### 1) Offline-отчётность и подготовка данных
Добавлены команды:
- `python -m app.cli coverage-audit`
- `python -m app.cli reporting-sqlite`
- `python -m app.cli training-dataset`

Добавлены модули:
- `app/reporting/coverage.py`
- `app/reporting/sqlite_export.py`
- `app/reporting/training_dataset.py`

Что это даёт:
- отдельный coverage audit по debug JSON;
- SQLite warehouse по state + history/runs;
- flat training dataset для последующей калибровки / retraining.

### 2) Self-history стал безопаснее
В self-history контексте теперь:
- отрицательные `expected_home/expected_away` санитизируются и не попадают в расчёт;
- архивный контекст выбирается с приоритетом неотрицательных xG;
- разрешён probability-only fallback, если xG нет, но есть валидные win-probabilities.

Это не гарантирует мгновенный рост `self_history_contexts_built`, но убирает ситуацию, когда historical layer засоряется плохими архивными xG.

### 3) Derived-market сигналу добавлен мягкий consensus-relief
Для derived-candidates без history-ready сигнала добавлен более мягкий проход:
- если есть стабильный 2-book consensus,
- низкая дисперсия,
- и хотя бы умеренный positive edge.

Это должно помочь totals/spreads получать derived-сигнал не только при наличии history-ready market monitor.

### 4) Диагностика провайдеров стала полезнее
В provider diagnostics summary теперь отдельно выводятся:
- `provider_rate_limits`
- `published_candidates_single_source_context`

Это помогает быстрее понимать, что реально режет coverage: фильтры модели или rate limit / single-source контекст.

## Что проверено
- `py_compile` для изменённых модулей — ок
- `coverage-audit` — ок
- `reporting-sqlite` — ок
- `training-dataset` — ок

## Ограничение
Полный end-to-end live run я здесь не запускал. Это fix-архив для применения в репозиторий и следующего боевого прогона.
