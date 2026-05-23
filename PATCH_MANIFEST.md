# sports-bot fast balanced depth v3

Fixes the fast workflow after `run-bot-fast-26328356590` still produced only 36 matches, 4 odds requests, 0 matches with 2+ books and no Bzzoiro secondary odds.

## Changes

- Keeps `RUN_MODE=normal` for app logic.
- Uses a 24h publish window in fast mode instead of 12h.
- Writes `.data/exports/latest-fast-run-env.sh` and sources it immediately before `app.cli run-once`, so effective fast-depth overrides apply in the same shell step.
- Maps `ODDS_API_IO_KEY2`, `ODDS_API_IO_ACC2_KEY`, and `ODDS_API_IO_SECONDARY_KEY` to the second odds-api.io secret alias.
- Forces four bookmaker coverage: account1 `Bet365,Unibet`, account2 `Betfair Exchange,Sbobet`.
- Raises balanced-fast odds/Bzzoiro depth without changing publication guards.
- Extends fast depth diagnostics to warn when account2/bookmaker depth is missing.

## Unchanged safety rules

- SStats remains context-only.
- Tier A still requires 2 independent odds sources.
- EV, edge, xG, quality and lifecycle guards are unchanged.
