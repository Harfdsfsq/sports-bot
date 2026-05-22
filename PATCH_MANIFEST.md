# sports-bot run263034 report v8/watchlist fix

## Что исправлено

- `scripts/send_harizon_telegram_run_report_v8.py` теперь имеет собственный `main()` и напрямую пишет/отправляет v8 payload.
  Раньше файл ставил v8 wrapper, но затем вызывал `v7.v5.main()`, из-за чего в части запусков Telegram и JSON оставались `HARIZON run report v7`.
- `scripts/publish_controlled_fallback.py` в no-pick watchlist больше не пишет неоднозначное `линий 2`.
  Теперь отдельно выводятся:
  - `цен` = bookmaker price confirmations;
  - `odds sources` = независимые провайдеры линий;
  - `контекст` = независимые контекстные подтверждения.

## По запуску 26303423515

Решение не публиковать Breidablik — KR было корректным:
- odds sources: `1/2`;
- context confirmations: `1/2`;
- candidate дошёл только до Tier C watch-only;
- SStats был контекстом, не line source.

Патч не ослабляет публикацию, а чинит отчёт и текст no-pick сообщения.
