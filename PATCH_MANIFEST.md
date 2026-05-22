# sports-bot run263078 day-inventory fallback membership fix

## Причина

В run-bot-26307847014 controlled fallback показал watchlist-кандидата Porto Vitoria ES — Audax Sao Mateus ES, но этот матч отсутствовал в текущем frozen day inventory на 2026-05-22. Из-за этого Telegram-watchlist мог показывать кандидата с `цен 2, контекст 2`, а `Coverage truth` и `Current day inventory windows` одновременно показывали `price 2+: 0` и `context 2+: 0` в ближайшем окне.

## Исправление

- `scripts/publish_controlled_fallback.py`
  - добавлена проверка `CONTROLLED_FALLBACK_REQUIRE_DAY_INVENTORY_MEMBERSHIP=true` по умолчанию;
  - fallback pool теперь берёт только кандидатов, которые есть в `.data/day_inventory/{DAY_INVENTORY_TARGET_DATE}.json` / `current.json` / `latest.json` / `today.json`;
  - для совместимости матч проверяется и по `match_key`, и по нормализованной паре команд + дате;
  - в `pool_counts` добавляется `*_not_in_day_inventory`, чтобы причина была видна в no-pick report.

- `tests/test_controlled_fallback_day_inventory_membership.py`
  - регресс-тест на отбрасывание кандидата вне day inventory;
  - регресс-тест на сохранение кандидата внутри day inventory.

## Что не менялось

- Финальные odds-source/context/value/quality guards не ослаблены.
- SStats не становится источником линий.
- Single-provider odds signal не публикуется как Tier A.
