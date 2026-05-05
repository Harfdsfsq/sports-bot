# API reliability and matching improvement layer

This update adds a safe runtime layer for API coverage, market integrity, and quick provider diagnostics.

## Added components

### `scripts/api_health_run.py`

Manual/CI health run for all configured providers. It checks credentials, endpoint availability, HTTP status, useful row count, and rate-limit/auth failures without publishing picks.

Outputs:

- `.data/exports/latest-api-health-run.json`
- `.data/exports/latest-api-health-run.md`

Usage:

```bash
python scripts/api_health_run.py --mode quick
python scripts/api_health_run.py --mode deep --fail-on-critical
```

`quick` checks auth and lightweight endpoints. `deep` also performs tiny odds samples for odds-api.io accounts and SharpAPI.io candidate endpoint.

### `.github/workflows/api-health.yml`

Manual GitHub Actions workflow named **API Health Run**.

Inputs:

- `mode`: `quick` or `deep`
- `fail_on_critical`: fail workflow when core providers are unusable

Critical providers in the health report:

- `odds_api_io_events`
- `bookies_api`
- `sportlogic`
- `sstats`
- `bzzoiro`

### `app/services/match_identity.py`

Canonical match identity layer for cross-provider matching. It normalizes:

- team names;
- league names;
- start time;
- women/youth/reserve/simulated tags;
- swapped home/away cases.

It returns a weighted score and a quality bucket:

- `exact`
- `strong`
- `fuzzy`
- `reject`

This is intended for provider matching upgrades and debugging.

### `app/services/market_integrity.py`

Candidate-level guard against bad odds/market parsing. It blocks:

- totals without points;
- FT totals polluted by corners/HT markets;
- suspicious low-total prices such as Over/Under 1.5 with unrealistic odds unless confirmed by enough market depth;
- spreads while handicap parser is quarantined;
- team totals unless explicitly enabled;
- single-source market fallback candidates without enough books.

The guard is installed automatically through `usercustomize.py`.

### `app/services/api_runtime_enhancements.py`

Startup defaults for safer provider behavior:

- odds-api.io dual-account bookmaker split;
- BookiesAPI auto-enable when credentials exist;
- TheSportsDB free key fallback;
- external signal defaults;
- market integrity defaults;
- conservative SportLogic controlled mode.

## Runtime behavior

The default production run remains conservative. The new layer does not force SportLogic odds into production unless:

```env
SPORTLOGIC_CONTROLLED_ODDS_ENABLED=true
```

BookiesAPI is enabled automatically only when credentials exist.

The market integrity guard is enabled by default:

```env
MARKET_INTEGRITY_HARD_GUARD_ENABLED=true
MARKET_INTEGRITY_CANDIDATE_PATCH_ENABLED=true
```

## Recommended next checks

1. Run **API Health Run** in `quick` mode.
2. Check whether `odds_api_io_events` is OK and whether `ODDS_API_IO_KEY_2` is present.
3. Run `deep` mode to verify account1/account2 odds samples.
4. If `bookies_api` is OK, keep it as independent odds source on shortlist.
5. If `sportlogic` is OK and inventory is fresh, test with `SPORTLOGIC_CONTROLLED_ODDS_ENABLED=true`.
6. If `sharpapi_io_odds_candidate` is OK in deep mode, split SharpAPI.com text enrichment from SharpAPI.io odds provider.

## Expected improvement

The bot now has a fast way to identify whether a provider is missing credentials, rate-limited, auth-failing, or returning no useful rows. Main runtime also gets stricter candidate filtering so suspicious totals and single-source market fallback picks are blocked before Telegram publication.
