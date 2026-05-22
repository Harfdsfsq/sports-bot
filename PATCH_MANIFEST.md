# sports-bot run 262931 follow-up patch

## Причина
Свежий запуск 22.05.2026 17:43 показал две проблемы:

1. `latest-progressive-active-core-budget-patch.json` уже исключил SportLogic при `grant=0`, но Telegram v8 успел отрендерить старый progressive contract и показал SportLogic как active core.
2. В controlled fallback попал reserve-кандидат с отрицательной canonical value после quality/historical calibration (`EV -15.5%`). Ранее pre-quality значение было положительным, потому что `canonical_adjusted_probability` сохранялся до качества.

## Изменения
- `scripts/send_harizon_telegram_run_report_v8.py` теперь сам мержит `latest-progressive-active-core-budget-patch.json` в progressive contract перед рендером и печатает `Active core ...` + `Excluded from active core`.
- `scripts/publish_controlled_fallback.py` фильтрует fallback-pool по post-quality canonical EV/edge до evaluation, чтобы negative-value rescue rows не попадали в Telegram no-pick reserve list.
- `app/services/candidate_value_runtime_patch.py` предпочитает `diagnostics.quality.final_adjusted_probability` при пересчёте canonical value.
- `app/services/__init__.py` безопасно устанавливает active-core и rescue-consensus patches.

## Что не ослаблено
- Negative EV всё ещё блокируется.
- `bad_historical_segment_guard` всё ещё блокирует публикацию.
- SStats не становится источником линий.
- SportLogic не считается active core при zero budget.
