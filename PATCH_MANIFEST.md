# sports-bot-run262984-tierb-discovery-fix

## Причина
В запуске `run-bot-26298444638` CandidateFactory не был реально пустым:
- `core_line_bookmaker_universe.candidates_after = 5`
- `model_input_market_sanity.candidates_after = 5`
- `controlled_consensus_rescue.built = 40`
- `market_integrity_guard.remaining_candidates = 18`

Но затем `market_family_publication_guard` в candidate-discovery стадии заблокировал все строки с `insufficient_publication_odds_sources:1<2`, поэтому `candidate_value_runtime_patch` получил `input_candidates = 0`, а fallback увидел `0` кандидатов.

## Правка
- В candidate-discovery стадии разрешён hybrid Tier B проход: `1 real line source` для `totals/spreads`, чтобы кандидат дошёл до quality/fallback.
- В TelegramPublisher/text stage строгий guard остаётся: финальная публикация не ослаблена.
- Runtime chain теперь устанавливает `post_integrity_candidate_rescue` в основном run.
- v8 report добавляет CandidateFactory diagnostics, когда raw pool = 0.

## Безопасность
SStats не становится line source. Публикация всё ещё проходит финальные проверки EV/edge/xG/context/books/quality/line movement.
