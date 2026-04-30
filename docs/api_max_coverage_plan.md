# API max coverage and matching improvement plan

## Scope

Goal: make every enabled source produce the maximum useful value without lowering prediction quality.

Inputs checked:

- Uploaded GitHub Actions logs archive `logs_66923412981.zip`.
- Current branch `fix/sportlogic-api-providers`.
- Provider implementations under `app/providers/`.
- Runtime/budget scripts under `scripts/` and `config/provider_request_budget.json`.

External live documentation was not reachable from this ChatGPT session because web access was disabled. This plan therefore validates request correctness from code, logs, observed HTTP statuses, and known endpoint shapes in the repository. A later pass should compare each endpoint against the current vendor docs when web access is available.

## Current status matrix

| Provider | Current request correctness | Matching correctness | Main blocker | Priority |
|---|---:|---:|---|---:|
| odds-api.io | Good: `/v3/events` and `/v3/odds/multi` return 200 and thousands of offers | Good: 75 events matched, 41 with offers | no fallback to account2 for event bootstrap; only four bookies used | P0 |
| SportLogic | Adapter exists, but not active in inspected run | Defensive parser exists, unverified live | missing direct workflow/budget grant path and `SPORTLOGIC_API_KEY` secret | P0 |
| SStats | Good HTTP 200, but too broad historical scans | Medium: direct exact/loose/fuzzy 0, team-form fallback works | huge unmatched rows, weak alias reuse | P0 |
| Bzzoiro | Good: `/events/` and `/predictions/` return 200 | Good but narrow: 2 contexts from 18 predictions | natural coverage narrow + league mismatch rejection | P1 |
| AllSportsAPI | Code endpoint shape is reasonable; not active in inspected run | Untested in latest run | disabled by spacing/budget before runner | P1 |
| football-data.org | Good HTTP 200, but narrow competitions | Low in latest run: 0 matched | free plan coverage mismatch vs long-tail odds schedule | P2 |
| TheSportsDB | Good HTTP 200 | Medium-low: 2 contexts, table rows often missing | league/team alias map and table-season reuse | P2 |
| OpenFootball | Request path is syntactically valid, but dataset keys wrong for latest run | None because datasets 404 | invalid competition/season path probing | P2 |
| WeatherAPI/OpenWeatherMap | Endpoint code is normal | Depends on fixture location | budget/key/venue metadata; report reason misleading | P2 |
| News/GNews/Futrix/OddsFeed/Sportsbook | Not broken; mostly budget/spacing-disabled | mostly not in runner path | quota governor rotates them out | P3 |

## P0 — must fix first

### 1. Directly wire SportLogic into workflow and budget policy

Problem:

- `SportLogicProvider` is implemented as a runner-compatible async adapter.
- The inspected run did not use it because the workflow did not expose `SPORTLOGIC_API_KEY`, and provider budget did not grant requests.

Changes:

1. Add to `.github/workflows/run-bot.yml`:

```yaml
SPORTLOGIC_API_KEY: ${{ secrets.SPORTLOGIC_API_KEY }}
ENABLE_SPORTLOGIC: "true"
SPORTLOGIC_ENABLED: "true"
SPORTLOGIC_PER_RUN_MAX: "80"
SPORTLOGIC_MATCH_LIMIT: "80"
SPORTLOGIC_ODDS_MATCH_LIMIT: "40"
```

2. Add `sportlogic` to `config/provider_request_budget.json`:

```json
"sportlogic": {
  "enabled": true,
  "per_run_max": 80,
  "safe_daily_budget": 500,
  "min_spacing_minutes": 0,
  "secret_env_keys": ["SPORTLOGIC_API_KEY"],
  "env": {
    "ENABLE_SPORTLOGIC": "true",
    "SPORTLOGIC_ENABLED": "true",
    "SPORTLOGIC_PER_RUN_MAX": "80",
    "SPORTLOGIC_MATCH_LIMIT": "80",
    "SPORTLOGIC_ODDS_MATCH_LIMIT": "40"
  },
  "disable_env": {
    "ENABLE_SPORTLOGIC": "false",
    "SPORTLOGIC_ENABLED": "false",
    "SPORTLOGIC_PER_RUN_MAX": "0",
    "SPORTLOGIC_MATCH_LIMIT": "0",
    "SPORTLOGIC_ODDS_MATCH_LIMIT": "0",
    "SPORTLOGIC_API_KEY": ""
  }
}
```

3. Add `sportlogic` to `HARIZON_CRITICAL_PROVIDERS` and `HARIZON_RECOVERY_GRANTS` in `scripts/apply_provider_request_budget.py`.

Expected gain:

- Additional fixture/context/odds source begins to contribute to `matches_with_offers`, `contexts_built`, and `sources_count`.

Validation:

- Detailed report must show `sportlogic` and `sportlogic_context`.
- `api_key_present=true`.
- `requests>0` if the secret exists.
- `offers_parsed>0` or explicit diagnostic reason if API shape differs.

### 2. Fix odds-api.io account failover and bookmaker expansion

Current code:

- Bootstrap uses `/v3/events` with `sport=football`, `status=pending,live`, `from`, `to`, `limit`, `page`.
- Offers use `/v3/odds/multi` with `eventIds` and account-specific `bookmakers`.
- Latest logs show this works: 3,447 offers parsed and 41 matches with lines.

Problems:

- Event bootstrap is effectively account1-first; if account1 is rate-limited, account2 does not fully rescue events.
- The system currently requests only `Bet365,Unibet,Betfair Exchange,Sbobet`. This is intentional for quality, but it leaves coverage on the table.

Changes:

1. Add event-bootstrap failover:
   - if account1 `/events` fails/rate-limits, retry `/events` with account2 key.
   - keep same event IDs and dedupe.
2. Add optional secondary bookmaker set:
   - `ODDS_API_IO_BOOKMAKERS_ACCOUNT3` only if another key exists.
   - do not mix weak bookmakers into consensus unless they are marked as `soft_books`.
3. Add per-match line coverage buckets:
   - `2plus_sharp_books`, `1_sharp_book_plus_soft_book`, `only_soft_books`.

Expected gain:

- More matches with 2+ bookmakers, fewer runs where candidates die on `books_below_min`.

Validation:

- `matches_with_2plus_books` increases.
- `bookmakers_seen_names` grows only if configured.
- No increase in false positives because quality still requires books/EV/context.

### 3. Replace SStats broad scan with target-team index and cache

Current code:

- Queries `https://api.sstats.net/Games/list` with `from`, `to`, `limit=1000`, `offset`, `apikey`.
- Logs show 23 HTTP 200 requests and 18,837 unmatched rows.
- Context is built mainly through team-form fallback, not direct event matching.

Problems:

- Too many irrelevant historical rows.
- Direct matching counters are 0 in the inspected run.
- Every run repeats similar historical scans.

Changes:

1. Build daily SStats cache:
   - key: `sstats:{from}:{to}:{chunk_days}`.
   - TTL: 12h for historical windows, 1h for current-week rows.
2. Pre-filter rows before expensive matching:
   - extract canonical team keys for all odds-backed matches.
   - keep rows where either team similarity to any target team >= 0.55.
3. Split stats:
   - `rows_fetched_raw`
   - `rows_after_team_prefilter`
   - `direct_event_contexts_built`
   - `team_form_contexts_built`
   - `unmatched_due_to_window`
   - `unmatched_due_to_team`
4. Alias learning:
   - when SStats row contributes to team-form, store alias pair `odds_name -> sstats_name` if similarity >= threshold.
   - reuse aliases in later runs.

Expected gain:

- Same or better context count with fewer requests and much cleaner diagnostics.
- Higher confidence for context because fewer weak fallback contexts.

Validation:

- `rows_after_team_prefilter / rows_fetched_raw` should drop below 30%.
- `contexts_built` should stay >= current baseline.
- `team_form_contexts_built` remains, but direct matches should appear when provider has current fixtures.

## P1 — coverage expansion without quality loss

### 4. Improve Bzzoiro matching and reduce duplicate work

Current code:

- Calls `/events/` and `/predictions/` with `date_from`, `date_to`, `tz=UTC`, plus `upcoming=true` for predictions.
- Uses `Authorization: Token <key>`.
- HTTP works and returns predictions.

Problems:

- Coverage is narrow by nature.
- Fuzzy predictions are rejected when league aliasing fails.
- SStats provider also contains a Bzzoiro fallback path, creating duplicate Bzzoiro logic.

Changes:

1. Keep only one canonical Bzzoiro provider path in runner diagnostics.
2. Extract shared Bzzoiro parser/matcher into one module.
3. Expand league aliases for common names:
   - `Malta Premier League` variants.
   - `International Clubs` prefixes.
   - `Group/Girone/Championship round` suffixes.
4. Store `event_id -> prediction_id` link cache for the day.

Expected gain:

- More Bzzoiro contexts from the same 1–2 requests.
- Less duplicated provider stats.

Validation:

- `event_matches + fallback_prediction_matches` increases.
- `prediction_rejected_league_mismatch` decreases.

### 5. Activate AllSportsAPI in controlled rotation

Current code:

- Uses `https://apiv2.allsportsapi.com/football/` with `met=Fixtures`, `APIkey`, `from`, `to`, `timezone=UTC`.
- Odds request uses `met=Odds`, `matchId`.
- Parser supports 1X2, totals, double chance, BTTS, AH/DNB.

Problem:

- Latest run had `ALLSPORTSAPI_PER_RUN_MAX=0` due spacing, so it never ran.

Changes:

1. Change budget policy from 120–240 minute spacing to strategic slots:
   - allow at 00:00 full inventory build.
   - allow once around 12:00 or manual recovery.
   - block all other runs unless `matches_with_offers` is below target.
2. Add recovery trigger:
   - if odds-api.io `matches_with_offers / matches_seen < 45%`, grant AllSportsAPI even if spacing active.
3. Cache fixture list for 6h and odds for 2h.

Expected gain:

- Fills odds gaps when odds-api.io coverage is thin.

Validation:

- In normal runs: low or zero requests.
- In low-coverage runs: `allsportsapi.requests>0`, `offers_parsed>0`.

## P2 — selective context providers

### 6. Make football-data.org selective, not broad

Current code:

- Calls `/v4/matches` with `dateFrom`, `dateTo`, `status=SCHEDULED,TIMED`, `limit=200`.
- On matched competitions, calls `/competitions/{ref}/standings` and `/competitions/{ref}/matches`.

Problem:

- Latest run fetched 8 matches, matched 0. This is expected because free/registered football-data.org coverage is narrow and often top-competition only.

Changes:

1. Pre-check target leagues before making `/matches` request:
   - only run if odds-backed matches contain football-data-supported competition hints.
2. If no supported hints, skip with reason `no_supported_competitions_in_target_set` instead of spending request.
3. When matched competitions exist, use competition-specific `/competitions/{code}/matches` before global `/matches`.

Expected gain:

- Fewer wasted requests.
- Better top-league contexts.

Validation:

- `requests` falls on long-tail days.
- `contexts_built / requests` improves.

### 7. Improve TheSportsDB league/table reuse

Current code:

- Calls `/all_leagues.php` then `/lookuptable.php?l=<idLeague>`.
- League and team normalization is already present.

Problems:

- Repeats `all_leagues.php` every run.
- Does not persist league ID resolution.
- Team rows often missing.

Changes:

1. Cache `all_leagues.php` for 7 days.
2. Cache `league_name -> idLeague` resolutions for 7 days.
3. Cache tables per `idLeague` for 6h.
4. Add table-season parameter if available after docs verification.
5. Add alias learning from successful/near-miss row matches.

Expected gain:

- Same or more contexts with fewer requests.
- Better team table matching.

Validation:

- `requests` drops.
- `missing_table_rows` drops.
- `contexts_built` does not drop.

### 8. Fix OpenFootball dataset probing

Current code:

- Builds raw paths like `/{season}/{comp_key}.json` against `football.json/master`.
- Latest run probed `2025-26/no.1.json`, `2026/no.1.json`, `2025/no.1.json` and got 404.

Problem:

- Competition/season map is too optimistic and blindly probes missing paths.

Changes:

1. Add `openfootball_dataset_manifest.json` with known-valid competition keys/seasons.
2. Add 404 cache:
   - key: `season + comp_key`.
   - TTL: 24h.
3. Do not call raw GitHub if key is known missing.
4. Mark source as `dataset_not_available` instead of `response_errors` for 404.

Expected gain:

- Avoid dead calls.
- Use OpenFootball only when dataset is known valid.

Validation:

- `404` requests near zero after first discovery.
- `datasets_loaded > 0` only for supported leagues.

### 9. Weather: fix key/budget/venue path

Current code:

- WeatherAPI endpoint: `https://api.weatherapi.com/v1/forecast.json` with `key`, `q`, `days=2`, `aqi=no`, `alerts=no`.
- OpenWeather endpoint: `https://api.openweathermap.org/data/2.5/forecast` with `q`, `appid`, `units=metric`, `cnt=16`.

Problems:

- Latest run had 0 weather budget and report collapsed it into `missing_api_key`.
- Weather location quality depends on fixture venue metadata; odds-api.io often does not carry venue/city.

Changes:

1. Report reason priority:
   - `budget_zero` / `daily_budget_exhausted`
   - `missing_api_key`
   - `missing_location`
   - `no_weather_payload`
2. Add league-country fallback map to build query from `home_team + country`.
3. Cache weather query failures for 12h.
4. Prefer WeatherAPI if key exists; OpenWeather fallback only when WeatherAPI fails.

Expected gain:

- Weather starts contributing when keys/budget exist.
- No misleading diagnostics.

Validation:

- `weather.reason` becomes actionable.
- `weather.contexts_built > 0` for matches with known country/city.

## P3 — monitoring and rotation improvements

### 10. Provider classification report

Add a report section that categorizes every provider as exactly one:

- `working_data_used`
- `working_low_overlap`
- `disabled_by_budget`
- `disabled_missing_key`
- `rate_limited`
- `parser_or_schema_suspect`
- `not_wired_to_runner`

This prevents confusing “API не работает” with “budget skipped it”.

### 11. Request correctness audit script

Create `scripts/provider_endpoint_audit.py`:

- dry-runs each provider with 1–3 selected matches;
- records URL path, params names, auth header style, status code, payload shape;
- writes `.data/exports/latest-provider-endpoint-audit.json`;
- never publishes picks.

### 12. Matching audit script

Create `scripts/provider_matching_audit.py`:

- for every provider row, records best match score, best match key, league score, team scores, time diff;
- exports top rejected rows by `score >= threshold - 15`;
- builds alias suggestions automatically.

## Final target metrics

A healthy run should look like this:

- `matches_with_offers / matches_seen`: 60–80%+
- `matches_with_context / matches_with_offers`: 70–90%+
- `matches_ready_for_model`: close to `matches_with_context`
- `matches_with_2plus_books`: 50%+ of offer matches
- SStats unmatched raw rows after prefilter: below 30% of fetched rows
- Bzzoiro rejected league mismatch: below 30% of predictions
- OpenFootball 404: zero after first cached discovery
- Detailed report no longer lists removed `api_football`

## Implementation order

1. SportLogic direct env/budget wiring.
2. Provider classification report.
3. SStats cache + target-team prefilter.
4. odds-api.io account failover.
5. AllSportsAPI recovery-slot activation.
6. Bzzoiro shared matcher + alias expansion.
7. football-data selective trigger.
8. TheSportsDB cache + alias learning.
9. OpenFootball manifest + 404 cache.
10. Weather reason priority and location fallback.
