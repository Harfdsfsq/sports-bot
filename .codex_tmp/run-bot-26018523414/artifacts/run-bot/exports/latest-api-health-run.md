# API Health Run

- Created UTC: `2026-05-18T07:01:47.642357+00:00`
- Mode: `quick`
- Providers checked: **18**
- Removed providers: `api_football, bookies_api, oddspapi`
- OK: **16**
- Config-only: **1**
- Skipped: **0**
- Healthy/config/skipped: **17**
- Degraded: **0**
- Rate-limited: **0**
- Auth errors: **0**
- Missing secrets: **1**
- Critical failures: **0**

## Recommendations

- odds-api.io inventory is healthy; keep dual-account bookmaker split active.
- SportLogic is reachable; use controlled shortlist mode only after fixture freshness/matching checks.
- FutrixMetrics key is present, but live probe is skipped until FUTRIXMETRICS_BASE_URL and FUTRIXMETRICS_HEALTH_ENDPOINT are configured; this is not a runtime failure.
- TheSportsDB is reachable; use it for team/league alias enrichment.
- Removed providers are intentionally excluded: bookies_api, api_football, oddspapi.

## Provider results

| Provider | Group | Status | Requests | Useful rows | Message |
|---|---|---:|---:|---:|---|
| `bzzoiro` | `context` | `ok` | 1 | 32 | ok |
| `football_data` | `context` | `ok` | 1 | 13 | ok |
| `futrixmetrics` | `context` | `config_only` | 0 | 0 | key present; live probe skipped because FUTRIXMETRICS_BASE_URL/FUTRIXMETRICS_HEALTH_ENDPOINT are not configured |
| `highlightly` | `context` | `ok` | 1 | 5 | ok |
| `sstats` | `context` | `ok` | 1 | 5 | ok |
| `thesportsdb` | `context` | `ok` | 1 | 5 | ok |
| `currents` | `news` | `ok` | 1 | 30 | ok |
| `gnews` | `news` | `ok` | 1 | 1 | ok |
| `guardian` | `news` | `ok` | 1 | 1 | ok |
| `newsapi` | `news` | `ok` | 1 | 1 | ok |
| `newsdata` | `news` | `ok` | 1 | 1 | ok |
| `odds_api_io_events` | `odds` | `ok` | 1 | 10 | ok |
| `sportlogic` | `odds` | `ok` | 1 | 50 | ok |
| `allsportsapi` | `odds_context` | `ok` | 1 | 4 | ok |
| `sharpapi_configured_base` | `utility` | `missing_secret` | 0 | 0 | required secret is not configured |
| `meteostat` | `weather` | `ok` | 1 | 1 | ok |
| `openweathermap` | `weather` | `ok` | 1 | 0 | ok |
| `weatherapi` | `weather` | `ok` | 1 | 1 | ok |
