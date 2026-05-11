# Чистый цикл исправления API и provider-smoke

Цель: после каждого загруженного zip-лога делать не маленькую заплатку, а полный фикс цепочки: endpoint -> parser -> matching -> diagnostics -> runtime wiring.

## Обязательный порядок разбора лога

1. Проверить commit SHA и marker активного diagnostics route.
2. Проверить, какой файл реально запускался: provider_smoke_fast, provider_smoke_run или runtime run.
3. Проверить фактические API attempts: URL, params, HTTP status, payload shape, row count.
4. Проверить parser-stage: raw_rows, parsed_events, missing_team_rows, missing_start_rows.
5. Проверить matching-stage: eligible_events, matched_to_odds_inventory, failure_stage, samples.
6. Проверить wiring: созданный patch должен быть подключен к фактическому entrypoint, а не только лежать в репозитории.

## Правило для правок

Один фикс должен закрывать минимум четыре слоя:

- documented endpoint / параметры запроса;
- parser под фактическую форму payload;
- diagnostics fields в JSON/TXT;
- подключение в актуальный workflow entrypoint.

Если меняется только один слой, следующий прогон почти наверняка будет потрачен впустую.

## Что должен печатать provider-smoke для каждого проблемного API

Для request-stage:

- url;
- params_keys;
- http_status;
- payload_shape;
- body_preview без секретов;
- rows_count.

Для parser-stage:

- raw_rows;
- parsed_events;
- missing_team_rows;
- missing_start_rows;
- sample keys первых rows;
- sample extracted ids;
- sample normalized events.

Для matching-stage:

- eligible_events;
- matched_to_odds_inventory;
- match_rate_pct;
- matched_samples;
- unmatched_samples;
- best_score / best_quality;
- provider_norm / odds_norm.

## SportLogic policy

SportLogic нельзя чинить угадыванием. Используем только documented route:

1. `/games` с документированными date/status параметрами.
2. Если матчей нет: `/odds?is_active=true`.
3. Из active odds достать game_id/fixture_id/event_id рекурсивно.
4. По game_id запросить `/games/{id}`.
5. Парсить home/away/league/start из documented fields: `home_team`, `away_team`, `league`, `start_time` и вложенных объектов.
6. В TXT обязательно выводить `documented_active_odds_sample_keys` и `documented_active_id_candidates_sample`.

## SStats policy

SStats не считать только историческим источником, если документация и smoke показывают odds fields.

Порядок:

1. `/Games/list` как fixture + compressed odds/context.
2. Для выбранных game_id проверять `/Odds/{gameId}`.
3. Live endpoints использовать только в live-window, не в обычном prematch smoke.
4. В price-source засчитывать только при наличии валидных decimal odds > 1.0.

## Definition of Done

Фикс считается готовым только если следующий лог показывает:

- правильный marker route;
- нет recursion/import loop;
- нет parser_extract_failed без sample keys;
- raw_rows > 0 приводит либо к parsed_events > 0, либо TXT показывает точную причину почему нет;
- diagnostics JSON и TXT содержат одни и те же ключевые поля;
- patch подключен к фактическому entrypoint.
