# sports-bot run263003 fallback odds/context split fix

Причина патча: запуск 263003 опубликовал controlled fallback как `уровень A`, потому что `publish_controlled_fallback.py` считал `odds_sources_count` из общего `sources_count`. В результате контекстные источники (SStats/Bzzoiro/weather) могли раздувать line-source count, а Telegram показывал `bzzoiro, sstats` как подтверждения линии.

Изменения:
- `scripts/publish_controlled_fallback.py`
  - odds-source truth берётся из `publish_coverage_contract`, а не из общего `sources_count`;
  - SStats/ClubElo/weather/context_equiv не могут увеличивать `odds_sources_count`;
  - account-level odds-api.io sources считаются price confirmations, но не независимыми provider sources;
  - Tier A требует 2 provider odds sources;
  - Tier B допускает 1 provider source только при 2+ books, 3+ context, повышенном EV/edge/confidence;
  - Telegram wording разделяет `Линии` и `контекст`.
- `scripts/send_harizon_telegram_run_report_v8.py`
  - снова мержит active-core budget patch;
  - не добавляет SportLogic в active core, если он excluded/disabled;
  - возвращён блок `Current day inventory windows`.
- `tests/test_controlled_fallback_odds_context_split.py`
  - регрессия на кейс ACF Fiorentina — Atalanta: SStats остаётся контекстом и не превращает 1 odds provider в Tier A.

Проверка:
```bash
python3 -m py_compile scripts/publish_controlled_fallback.py scripts/send_harizon_telegram_run_report_v8.py app/services/market_family_publication_guard.py app/services/runtime_startup_chain.py app/services/post_integrity_candidate_rescue.py app/services/__init__.py
PYTHONPATH=. python3 -m pytest tests/test_controlled_fallback_odds_context_split.py -q
```
