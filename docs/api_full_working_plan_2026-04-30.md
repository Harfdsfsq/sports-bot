# Full API working analysis and operating plan — 2026-04-30

Source run: GitHub Actions artifact `logs_66951790921.zip`, Telegram report at 21:16 MSK, repository `main`.

## 1. Executive conclusion

The bot is no longer failing because APIs are globally unavailable. The latest inspected run proves that the main data layer works:

- 45 matches scanned in the run.
- 41/45 had odds.
- 38/45 had merged context.
- Day inventory stayed at 210 matches, 106 with odds, 106 with context, 106 ready for model.
- Weather started working again and enriched 25 matches.
- SStats and Bzzoiro both produced useful context.

The current blocker is architectural:

> The publication/fallback layer uses `sources_count=1` because it counts the odds provider/source, not the independent context confirmations.  At the same time the debug payload shows real context combinations such as `sstats+weather`, `sstats+bzzoiro+weather`, and `sstats+bzzoiro+football_data+weather`.

As a result, candidates are rejected with `controlled fallback sources below min:1/2` even when the match has several independent context providers.

The fix is not to lower quality.  The fix is to separate two concepts:

1. `odds_sources_count`: number of market/line providers.
2. `confirmation_sources_count`: number of independent context/confirmation providers.

Controlled fallback should require:

- enough bookmaker/line support, and
- enough independent confirmation support,

not only `odds_api_io` as a source.

## 2. Latest run API facts

### Run-level coverage

| Metric | Value |
|---|---:|
| Run matches | 45 |
| Matches with odds | 41 |
| Matches with any context | 38 |
| Merged contexts | 38 |
| Raw candidates | 4 |
| Candidates before quality/fallback | 7 |
| Publishable | 0 |
| Fallback selected | 0 |

### Day inventory

| Metric | Value |
|---|---:|
| Total day matches | 210 |
| With odds | 106/210 |
| With context | 106/210 |
| Ready for model | 106/210 |
| Next 6h ready | 5/9 |
| Next 12h ready | 5/9 |

Inventory is now stable and no longer the main issue.

## 3. Provider-by-provider analysis

### 3.1 odds-api.io

Latest run:

- Data: 41 matches with lines.
- Offers: 4,934.
- Grant/max: 200.
- Account 1: 5 requests, 1,141 offers.
- Account 2: 5 requests, 3,793 offers.
- Bookmakers seen: Bet365, Betfair Exchange, Sbobet, Unibet.
- Matches with 2+ bookmakers: 37.
- Matches with 1 bookmaker: 4.

Status: excellent.

Role:

- Primary fixture/odds universe.
- Primary market consensus.
- Best source for line depth.

Problem:

- It is only one provider source even when it contains many bookmakers.
- Fallback currently treats candidate `sources_count` as 1 because lines come from `odds_api_io` only.

Plan:

1. Keep odds-api.io as primary bootstrap and odds source.
2. Continue two-account split.
3. Use bookmaker count as `books_count`, not as provider confirmation count.
4. Add market-depth labels:
   - `sharp_books_count`
   - `total_books_count`
   - `exchange_present`
   - `soft_books_present`
5. Keep publication quality strict, but avoid rejecting strong candidates only because `odds_provider_count=1` if independent context confirmations exist.

### 3.2 SportLogic

Latest run/logs:

- Base/API auth are now correct: `https://api.sportlogic.io/api/v1`, `X-API-Key`.
- Games endpoint returns HTTP 200.
- Fixtures fetched: 200.
- Events matched: 15.
- Odds endpoint requests: 17.
- Odds endpoint statuses: HTTP 200.
- Offers parsed: 0.
- Contexts built: 0.

Status: connected, but not useful yet.

Meaning:

- This is no longer an auth/base URL failure.
- The provider can fetch games and match events, but odds parsing is not extracting usable offers.
- Either `/games/{id}/odds` returns an empty payload for these games, or the payload shape is different from parser assumptions.

Plan:

1. Add payload diagnostics for SportLogic odds:
   - `odds_payload_empty_count`
   - `odds_payload_top_level_keys`
   - `odds_rows_count_before_parse`
   - `odds_rows_count_after_parse`
   - sample sanitized body preview per distinct shape.
2. Try both documented odds paths:
   - `/games/{id}/odds`
   - `/odds?game_id={id}`
3. Fetch `/markets` once per run/cache day and map market keys to bot families.
4. Support all likely flat odds shapes:
   - `option_name`, `option_value`, `odds`, `market.key`, `bookmaker.name`
   - `name`, `line`, `price`
   - nested `bookmaker -> market -> outcome`
5. If SportLogic gives only fixtures without usable odds, use it as context/source confirmation only after adding fixture-level data extraction.

Expected result:

- Either SportLogic becomes the second odds source, or the report clearly says why it cannot produce odds for the current games.

### 3.3 SStats

Latest run:

- Data: 38/38 contexts.
- Requests: 27.
- Max: 150.
- Exact matches: 5.
- Fuzzy matches: 3.
- Team-form contexts: 30.
- Unmatched rows scanned: 22,080.

Status: working and high-value, but inefficient.

Problem:

- SStats uses broad historical windows and scans many unmatched rows.
- It produces contexts, but too much work is spent on rows that cannot match the target fixtures.
- It is not used enough as targeted near-miss confirmation.

Plan:

1. Keep per-run cap at 150.
2. Replace broad-first scanning with target-first scanning:
   - read odds-backed matches;
   - read near-miss queue;
   - build target team aliases;
   - query recent windows only for teams/leagues that need confirmation.
3. Add a persistent team alias index:
   - `odds_api_io team -> sstats team`
   - league alias
   - country fallback
4. Split SStats diagnostics:
   - `event_contexts_built`
   - `team_form_contexts_built`
   - `rows_fetched_raw`
   - `rows_after_target_prefilter`
   - `rows_unmatched`
5. Store SStats context per match in persistent enrichment cache so the next run does not rescan identical history.

Expected result:

- More context for near-miss candidates with fewer irrelevant rows.
- Better confirmation scoring.

### 3.4 Bzzoiro

Latest run:

- Data: 11/11 contexts.
- Requests: 2.
- Max in report: 1000.
- Events fetched inside SStats fallback path: 40.
- Matched fuzzy: 11.
- Unmatched rows: 29.

Status: very useful and underused.

Problem:

- It only made 2 requests despite a very high cap.
- It returns useful data, but likely only page 1 is being used.
- Matching is mostly fuzzy, not exact.

Plan:

1. Use Bzzoiro aggressively but targeted:
   - always fetch current date and next date;
   - paginate until empty or page cap;
   - prioritize near-miss queue teams and leagues.
2. Increase max pages to 80, but stop early when pages are empty or duplicate.
3. Improve exact matching with alias map:
   - team normalized aliases;
   - league aliases;
   - country/competition fallback.
4. Promote Bzzoiro to `confirmation_sources_count` when its prediction/context supports the same match direction or xG family.
5. Avoid double-counting Bzzoiro when it appears through both SStats fallback and the standalone provider.

Expected result:

- More near-miss candidates reach confirmation source count >= 2.

### 3.5 WeatherAPI/OpenWeatherMap

Latest run:

- Weather contexts: 25.
- Requests: 29.
- WeatherAPI requests: 24.
- OpenWeatherMap requests: 5.
- WeatherAPI enriched: 24.
- OpenWeatherMap enriched: 1.
- No weather payload: 4.
- Max: 24+12.
- Report says `budget_exhausted`, but this means the per-run weather budget was consumed, not that the provider is broken.

Status: working again.

Problem:

- Weather is useful contextual confirmation, but should not count as a full independent betting/source confirmation for every market.
- It should contribute more strongly to totals, BTTS, pace/conditions, and outdoor football, less to markets that weather does not affect.

Plan:

1. Keep WeatherAPI primary, OpenWeatherMap fallback.
2. Add weather signal classification:
   - `weather_supports_under`
   - `weather_supports_over`
   - `weather_neutral`
   - `weather_risk_flag`
3. Count weather as a confirmation source only when it materially supports the market direction.
4. Cache weather by city/date/kickoff block for 4–6h.
5. Add report distinction:
   - `per_run_cap_reached`
   - not `daily_budget_exhausted`.

Expected result:

- Weather improves totals/BTTS validation without inflating unrelated source count.

### 3.6 football-data.org

Latest run:

- Data: 4/4 contexts.
- Requests: 3.
- Max: 4.

Status: working, narrow coverage.

Problem:

- Good for top competitions; poor for long-tail schedule.
- It should not be used as broad filler.

Plan:

1. Use only if target matches contain supported competitions.
2. Store supported competition map.
3. Use as confirmation source if standings/recent form materially supports model direction.
4. Keep cap low: 4/run.

### 3.7 TheSportsDB

Latest run:

- Data: 3/3 contexts.
- Requests: 7.
- Max: 8.

Status: working but inefficient.

Problem:

- Several table calls repeat.
- League/team alias map is weak.

Plan:

1. Cache `all_leagues.php` for 7 days.
2. Cache league ID resolution for 7 days.
3. Cache standings tables for 6h.
4. Build alias suggestions from near-misses.
5. Count as confirmation only when table/form context is relevant to the market.

### 3.8 OpenFootball

Latest run:

- 0 requests, 0 contexts.

Status: currently harmless but inactive.

Plan:

1. Keep only manifest-driven calls.
2. Add 404 cache for missing dataset paths.
3. Use for historical context/training, not live confirmation unless dataset exists.

### 3.9 FutrixMetrics / GNews / NewsAPI / Currents

Latest run:

- Futrix/GNews were still spacing-skipped in the human report.
- Per-run-only policy now removes daily/monthly caps, but cooldown/spacing may still be active.

Status: secondary.

Plan:

1. Use only for near-miss candidates and high-profile clubs.
2. Do not let news alone create a pick.
3. Count as confirmation only if news is team-specific and directionally relevant:
   - injuries/suspensions;
   - rotation risk;
   - schedule/travel impact;
   - coach/team news.
4. Keep small caps:
   - GNews: 2/run.
   - NewsAPI/Currents: 4/run combined.
   - Futrix: 4/run.

### 3.10 OddsPapi / OddsFeed / Sportsbook API / SportAPI / FreeAPILiveFootballData / Meteostat

Status:

- OddsPapi is in fatal cooldown due `REQUEST_LIMIT_EXCEEDED`; keep cooldown unless key/plan changes.
- OddsFeed/Sportsbook/SportAPI/FreeFootball are probe/fill providers.
- Meteostat is fallback/historical weather, not live primary weather.

Plan:

1. Keep low per-run caps.
2. Use as provider endpoint audit/probe, not main decision source until stable schema confirmed.
3. If a provider proves useful, promote it from `probe` to `secondary odds/context`.

## 4. Why information is not “closing the holes” yet

### Root cause 1 — wrong source metric

The candidate/fallback layer reports:

- `sources_count = 1`

But debug context combinations show matches with:

- `sstats+weather`
- `sstats+bzzoiro+weather`
- `sstats+bzzoiro+football_data+weather`
- `sstats+espn+thesportsdb+weather`

Therefore the bot has context, but the fallback guard does not treat it as independent confirmation.

Fix:

- Introduce `confirmation_sources_count`.
- Continue reporting `odds_sources_count` separately.
- Fallback should reject only when both odds and confirmation support are weak.

### Root cause 2 — near-miss queue exists but must become the main enrichment target

The queue is now created after fallback, but provider targeting must consistently prioritize it before generic enrichment.

Fix:

- Every context provider target selector should rank near-miss items first.
- Next run should fetch confirmation for these candidates before scanning generic matches.

### Root cause 3 — SportLogic is connected but not parsed

SportLogic returns HTTP 200 and fixtures match, but offers parsed = 0.

Fix:

- Capture sanitized odds payload shapes.
- Add `/odds?game_id=` fallback.
- Expand parser.

### Root cause 4 — some contexts are not direction-aware

A match having weather/SStats context is not automatically market confirmation. The context needs to support the candidate direction.

Fix:

- Add directional confirmation flags per market:
  - totals over/under;
  - BTTS yes/no;
  - handicap/DNB;
  - 1X2 if reopened later.

## 5. Target architecture

### Layer A — Fixture and odds universe

Primary:

- odds-api.io.

Secondary/repair:

- SportLogic once parser is fixed.
- AllSportsAPI when odds-api.io has missing lines.
- OddsFeed/Sportsbook/SportAPI only after endpoint audit.

Outputs:

- `offers_by_match`
- `books_count`
- `odds_sources_count`
- `market_depth_score`

### Layer B — Match context/enrichment

Primary:

- SStats.
- Bzzoiro.
- WeatherAPI/OpenWeatherMap.

Secondary:

- football-data.org.
- TheSportsDB.
- FutrixMetrics.
- News APIs.

Outputs:

- `context_sources`
- `confirmation_sources`
- `directional_confirmation_score`
- `context_quality_score`

### Layer C — Candidate scoring

Candidate should carry:

```json
{
  "odds_sources_count": 1,
  "books_count": 4,
  "context_sources_count": 4,
  "confirmation_sources_count": 2,
  "directional_confirmation_sources": ["sstats", "weather"],
  "non_directional_context_sources": ["thesportsdb"]
}
```

Publication/fallback gates should use:

- line depth: `books_count >= 2` or `odds_sources_count >= 2`;
- confirmation: `confirmation_sources_count >= 2`;
- quality: existing EV/edge/confidence/xG checks.

## 6. Operating policy by run stage

### Start of day / first full run

Goal: build maximum coverage.

1. odds-api.io full inventory.
2. SStats broad but cached context for all odds-backed matches.
3. Bzzoiro full current+next date pagination.
4. Weather for all matches with context/odds and outdoor football.
5. football-data/TheSportsDB for supported leagues.
6. Build day inventory and persistent context cache.

### Every normal 2-hour run

Goal: close remaining holes.

1. Load near-miss queue from previous run.
2. Target near-miss candidates first:
   - missing second confirmation;
   - single-source odds;
   - high EV/edge;
   - kickoff soon.
3. Run providers in this order:
   - odds-api.io refresh lines;
   - SportLogic odds repair;
   - Bzzoiro confirmation;
   - SStats targeted confirmation;
   - Weather directional context;
   - football-data/TheSportsDB if relevant;
   - news/Futrix only for high-value near-misses.
4. Build candidates.
5. If no publishable picks, build/update near-miss queue.
6. Persist queue and enriched contexts.

### Last 3 hours before kickoff

Goal: targeted repair only.

1. Do not rescan broad universe.
2. Use near-miss queue.
3. Refresh odds and weather.
4. Fetch Bzzoiro/SStats only for candidates that need confirmation.

## 7. Concrete implementation plan

### P0 — Fix false `sources_count=1` blocker

1. Add `confirmation_sources_count` to candidate metrics.
2. Derive it from merged context source list.
3. Add directional confirmation detection:
   - SStats/xG supports totals or BTTS direction.
   - Weather supports totals direction only if non-neutral.
   - Bzzoiro supports same pick family/direction.
   - football_data/TheSportsDB support form/standing context only when relevant.
4. Update fallback guard:
   - keep `books_count` guard;
   - replace `sources_count` guard with `confirmation_sources_count` guard.
5. Update Telegram report:
   - show `Линии: X | букмекеры: Y | подтверждения: Z`.

### P0 — SportLogic parser/diagnostics

1. Persist sanitized odds payload sample.
2. Add `/odds?game_id=` fallback.
3. Parse all discovered odds shapes.
4. Report:
   - fixtures fetched;
   - events matched;
   - odds payload rows;
   - offers parsed;
   - parse failures by market shape.

### P0 — Make near-miss queue drive provider targets

1. Ensure queue is built after every fallback check.
2. Ensure queue is synced persistently.
3. Ensure provider target selector prioritizes queue before generic matches.
4. Add report section:
   - queue loaded;
   - queue items targeted;
   - queue items closed;
   - queue items still missing confirmation.

### P1 — SStats target-first cache

1. Cache SStats rows per date-window.
2. Pre-filter by target teams.
3. Build team alias index.
4. Reduce unmatched rows while keeping 150/run capacity.

### P1 — Bzzoiro stronger use

1. Paginate beyond page 1.
2. Stop on empty/duplicate page.
3. Store event/prediction links.
4. Promote exact/alias matches over fuzzy.

### P1 — Weather as directional confirmation

1. Keep 24+12 per-run caps.
2. Add direction flags.
3. Do not overcount neutral weather.
4. Cache by location/date.

### P2 — Secondary providers

1. football-data.org only for supported competitions.
2. TheSportsDB cache tables/leagues.
3. Futrix/news only for near-misses/high-profile clubs.
4. Probe providers stay low-cap until proven useful.

## 8. Success metrics

A healthy run should reach:

| Metric | Target |
|---|---:|
| Run matches with odds | 80%+ |
| Run matches with context | 75%+ |
| Candidates with `confirmation_sources_count >= 2` | 50%+ |
| SportLogic offers parsed | >0 or explicit schema reason |
| SStats unmatched rows after prefilter | <30% raw rows |
| Bzzoiro requests | >2 when queue has unresolved items |
| Weather contexts | 20+ when weather-relevant matches exist |
| Near-miss queue closure rate | 30%+ per next run |

## 9. Current bottom line

The APIs are producing information.  The bot is not converting that information into candidate confirmation because the scoring/reporting layer still uses the wrong source concept.  Fixing `confirmation_sources_count`, SportLogic odds parsing, and near-miss-driven provider targeting is the correct path.  Lowering quality thresholds is not needed.
