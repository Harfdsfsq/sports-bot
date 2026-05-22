# HARIZON hybrid candidate factory follow-up

Причина патча: в запуске 22.05.2026 10:24 гибридные Tier B-флаги были включены, но candidate factory всё ещё работала как strict two-source layer (`allow_single_source=false`). Из-за этого большинство матчей с одним line provider и несколькими контекстами не попадали в пул controlled fallback.

Изменения:
- `MIN_SOURCES_PUBLISH=1` только для построения candidate pool; строгий publish-contract остаётся через `PUBLISH_MIN_ODDS_SOURCES=2` и fallback Tier A.
- Включён `CORE_LINE_BOOKMAKER_UNIVERSE_ALLOW_SINGLE_SOURCE=true` для гибридного режима.
- `MARKET_DERIVED_MIN_SOURCES=1`, чтобы market-derived кандидаты с одним line provider могли попасть в pre-quality pool.
- SStats удалён из `CORE_LINE_SOURCES`; он остаётся только контекстом.
- Bzzoiro и SportLogic остаются line sources только при реальных odds/offers.
- Добавлены регресс-тесты на single-line candidate factory policy.

Важно: этот патч не разрешает публиковать отрицательный EV или `bad_historical_segment_guard`. Он только позволяет Tier B увидеть больше кандидатов, после чего final fallback всё равно проверяет EV/edge/context/xG/quality.
