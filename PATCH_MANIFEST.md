# sports-bot hybrid source-gate follow-up

Причина патча: в запуске `run-bot-26275386700` флаг `CORE_LINE_BOOKMAKER_UNIVERSE_ALLOW_SINGLE_SOURCE=true` уже применился, но candidate pool всё равно остался `1`, потому что внутри `CandidateFactory` ещё действовали source-gates уровня `min_sources_publish` / `market_derived_min_sources`.

## Что изменено

- `app/services/core_line_bookmaker_universe_patch.py`
  - на время сборки кандидатов для Tier B временно снижает:
    - `min_sources_publish` до `1`;
    - `market_derived_min_sources` до `1`;
    - `market_derived_consensus_relief_min_sources` до `1`;
    - `line_movement_min_sources` до `1`;
  - после `build_candidates` возвращает старые значения;
  - пишет это в `latest-core-line-bookmaker-universe.json`.

- `tests/test_hybrid_candidate_build_relaxation.py`
  - регресс-тест на временное ослабление discovery-gates и восстановление настроек.

## Что НЕ ослаблено

- отрицательный canonical EV всё ещё блокирует публикацию;
- `bad_historical_segment_guard` всё ещё блокирует публикацию;
- xG-конфликт всё ещё блокирует публикацию;
- SStats не становится источником линий;
- Tier A остаётся строгим 2-line-source режимом;
- Tier B публикуется только через controlled fallback и его повышенные пороги.

## Проверка

```bash
PYTHONPATH=. python -m pytest tests/test_hybrid_candidate_build_relaxation.py -q
```
