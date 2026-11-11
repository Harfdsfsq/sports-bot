# History identity and NCAA coverage repair

## Observed defect

Run [43105842852](https://github.com/Harfdsfsq/sports-bot/actions/runs/43105842852)
retained an SStats form context for `CD FAS Santa Ana — Inter Santa Tecla` whose
home history belonged to `Santa Ana` in Costa Rica. A shared city-name subset
passed the former identity check. The separate `FAS — Inter` record had a verified
Salvadoran SStats fixture. These records are not automatically merged: the long
away alias still needs independent confirmation. No global `Inter` alias was added.

## Changes

- Historical matching rejects the deletion of distinctive name tokens. Reviewed
  abbreviations and spelling variants remain supported. This applies to form
  selection, targeted team-ID discovery, archive reuse and cached admission.
- Each newly constructed SStats form row preserves its provider team ID and
  country. A verified upcoming fixture binds history to that team's ID and
  provider name; a long display alias cannot override it. Conflicting IDs or
  countries are rejected. Old contexts without the necessary ID are recollected
  from raw history, not silently relabelled or assigned fresh timestamps.
- ESPN now routes the observed US college conference names to `usa.ncaa.m.1`.
  A reviewed alias catalogue binds 24 displayed names to both ESPN team ID and
  league namespace, and checks the provider name before expanding aliases.
- Daily ESPN matching checks both teams, home/away orientation and kickoff
  within 30 minutes. Multiple distinct matching events and live/final receipts
  are rejected. Women's/youth competitions cannot fall through to an adult
  men's league through substring matching.
- College form is built from the shared scoreboard response. There are no
  per-fixture probability or summary requests for NCAA. Missing bilateral form
  does not become a default context. Partial ESPN defaults are also rejected
  by admission. No invented sample count or measured xG was added.
- Forward validation V4 records the changed inputs. V3's declaration and trials
  remain in history. Fingerprints now include these provider/identity modules
  and the reviewed alias catalogue. Live publication gates stay closed.

## Primary-source check

The public ESPN scoreboard returned 20 events for the queried UTC/local date
bucket, including the previously uncovered college fixtures:

<https://site.api.espn.com/apis/site/v2/sports/soccer/usa.ncaa.m.1/scoreboard?dates=20261111&limit=1000>

A reduced receipt is kept in `tests/fixtures/espn_ncaa_scoreboard_20261111.json`.
An offline identity replay against the saved run inventory matched 11 of the
12 previously uncovered near-window NCAA fixtures. `Merrimack — Fairfield`
was rejected because ESPN had the opposite home/away orientation. This is an
identity replay, not a production coverage result or a betting backtest.

## Limits and next run

Local verification: 601 native regression tests passed, including 32 new cases;
Ruff and the changed-module mypy smoke passed. The full suite was also checked:
97 failed / 1,011 passed before the final four additional regression cases.
Its 97 failure node IDs exactly match the saved pre-patch local baseline;
the general CI debt is not declared fixed. All 32 new cases passed separately.

This patch supplies sporting context, not bookmaker lines for NCAA. Brazilian
state-cup history gaps, missing venue locations and the unresolved Salvadoran
duplicate remain visible. Strict cache admission can temporarily lower context
counts until raw histories are rebuilt. API credentials, quotas, bankroll,
settled results and Telegram publication settings are unchanged.

Run `run-bot` on `fix/coverage-integrity-a-only` with `dry_run=true`,
`price_watch_only=false`, `max_picks=2`. Check accepted/rejected SStats contexts,
ESPN assigned/matched events, near-window coverage, and the V4 declaration.
Do not count the offline matches as newly collected production contexts.
