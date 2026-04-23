# Интеграция в репозиторий

## 1. `app/services/calibration.py`

Подключить сервис в местах, где строится `CandidateBet`.

### Где встраивать

В `CandidateFactory` перед вызовом `_candidate_from_bucket(...)` для:

- totals
- h2h
- spreads
- simple_market_* fallback
- market_derived_*

### Минимальный паттерн

```python
from app.services.calibration import ProbabilityCalibrationService

# в __init__ CandidateFactory
self.calibration = ProbabilityCalibrationService(settings)

# перед _candidate_from_bucket(...)
calibrated_prob, calibration_meta = self.calibration.adjust(
    raw_probability=model_prob,
    market_probability=market_prob,
    family='h2h',
    odds=float(self._select_best_offer(bucket).price),
    confidence=float(getattr(context, 'confidence', 0.0) or 0.0),
    books_count=len({self._norm_book(item.bookmaker) for item in bucket}),
    sources_count=len({str(item.source or '').strip().lower() for item in bucket if str(item.source or '').strip()}),
    model_mode='soccer_context',
)
model_prob = calibrated_prob
```

После создания кандидата сохранить `calibration_meta` в `candidate.source_summary['calibration']`.

## 2. `app/services/guard_report.py`

Подключить в `runner.py` после этапов `build_candidates(...)` и `apply_to_candidates(...)`.

### Минимальный паттерн

```python
from app.services.guard_report import GuardReportService

self.guard_report = GuardReportService(settings)

# после forecast_rows / quality_debug / rejections
guard_report = self.guard_report.build_report(
    rejections=rejections,
    forecast_rows=forecast_rows,
    quality_decisions=quality_debug.get('decisions', []),
    context_summary={
        'matches_seen': len(filtered_matches),
        'matches_with_offers': sum(1 for m in filtered_matches if merged_offers.get(m.match_key)),
        'contexts_built': len(contexts),
        'raw_candidates': len(candidates_before_quality),
        'passed_quality': len(raw_candidates),
        'published': len(publishable_candidates),
    },
)
```

Потом передать `guard_report` в `sheet_export.write(...)`.

## 3. Замена `sheet_export.py`

Файл из архива совместим по базовому интерфейсу `write(...)`, но умеет принимать дополнительный аргумент:

```python
guard_report: dict[str, Any] | None = None
```

Если пока не хотите менять `runner.py`, файл всё равно будет работать — просто новые поля будут пустыми.

## 4. Куда сохранить профиль калибровки

Рекомендуемый путь:

```bash
.data/calibration-profile.json
```

Стартовать можно с `config/calibration-profile.example.json`, затем раз в день обновлять профиль по результатам накопленного ledger.

## 5. Как внедрять безопасно

### Stage A — только диагностика

- оставить текущую публикацию;
- включить новый export и guard-report;
- 3–5 дней просто копить данные.

### Stage B — мягкие env-изменения

- применить `recommended.env`;
- публиковать не более 1–2 ставок за run;
- держать `shadow_tracking` включённым.

### Stage C — калибровка

- включить `ProbabilityCalibrationService`;
- сравнить gap к рынку до/после;
- смотреть не только ROI, но и Brier/CLV/долю пустых прогонов.
