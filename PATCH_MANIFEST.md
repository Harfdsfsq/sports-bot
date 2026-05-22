# sports-bot run 262965 zero-candidate fix

Патч для запуска `run-bot-26296521425`.

## Что чинит

- `0 raw candidates` при наличии линий и контекста: подключает deterministic final CandidateFactory chain и post-integrity rescue.
- Hybrid Tier B: кандидаты с `1 line source + 2+ books + context` могут дойти до fallback evaluation, но финальная публикация всё ещё проверяет EV/edge/xG/context/quality.
- Отчёт v8 теперь отдельно показывает current day-inventory windows, чтобы cumulative progressive cache не маскировал реальные 0–4ч/0–12ч окна.

## Файлы

- `app/services/__init__.py`
- `app/services/post_integrity_candidate_rescue.py`
- `scripts/send_harizon_telegram_run_report_v8.py`
- `scripts/publish_controlled_fallback.py`
- `app/services/candidate_value_runtime_patch.py`
