Patch contents
==============

This archive contains a focused debug patch for BookiesAPI odds parsing.

Files included:
- app/providers/bookies_api.py

What changed
------------
1. Added rich debug for odds payloads in `_fetch_odds_for_game()`:
   - payload shape
   - top-level keys
   - lengths / nested keys for `data`, `results`, `response`, `bookmakers`, `odds`, `markets`, `values`, `games_pre`
   - task, game_id, and matched event info
   - raw body preview up to 2000 chars

2. Added a guard in `_parse_odds_payload()`:
   - if the endpoint returns `games_pre` instead of odds, parser exits early with no offers

3. Wired in the existing config limit `MAX_MATCHES_FOR_ODDS_FETCH`:
   - candidate matches are now capped before odds fetches
   - if the cap is applied, `candidate_matches_limited_to` appears in stats

Recommended debug run
---------------------
For a fast diagnostic run, set:
- `MAX_MATCHES_FOR_ODDS_FETCH=30`

Then run:
- `python -m app.cli run-once`

After the run, inspect:
- `source_stats.bookies_api.last_body_preview`
- `source_stats.bookies_api.payload_shapes`
- `source_stats.bookies_api.offers_parsed`
- `source_stats.bookies_api.candidate_matches_limited_to` (if present)

Notes
-----
- This patch does not change the final odds parser logic yet.
- It is meant to reveal the real shape of the BookiesAPI odds response so the parser can be adapted precisely.
