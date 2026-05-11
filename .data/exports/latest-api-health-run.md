# API Health Run

- Created UTC: `2026-05-11T19:01:38.506744+00:00`
- Mode: `quick`
- Providers checked: **18**
- Removed providers: `api_football, bookies_api, oddspapi`
- OK: **13**
- Config-only: **1**
- Healthy or config-only: **14**
- Degraded: **0**
- Rate-limited: **2**
- Auth errors: **1**
- Missing secrets: **1**
- Critical failures: **1**

## Recommendations

- odds-api.io inventory is healthy; keep dual-account bookmaker split active.
- Highlightly is not healthy on the current endpoint; keep it as optional context until endpoint/key is verified.
- FutrixMetrics key is present, but live probe is skipped until FUTRIXMETRICS_BASE_URL and FUTRIXMETRICS_HEALTH_ENDPOINT are configured; this is not a runtime failure.
- TheSportsDB is reachable; use it for team/league alias enrichment.
- Removed providers are intentionally excluded: bookies_api, api_football, oddspapi.

## Provider results

| Provider | Group | Status | Requests | Useful rows | Message |
|---|---|---:|---:|---:|---|
| `bzzoiro` | `context` | `ok` | 1 | 48 | ok |
| `football_data` | `context` | `ok` | 1 | 13 | ok |
| `futrixmetrics` | `context` | `config_only` | 0 | 0 | key present; live probe skipped because FUTRIXMETRICS_BASE_URL/FUTRIXMETRICS_HEALTH_ENDPOINT are not configured |
| `highlightly` | `context` | `rate_limited` | 1 | 0 | http_status=429 |
| `sstats` | `context` | `ok` | 1 | 5 | ok |
| `thesportsdb` | `context` | `ok` | 1 | 5 | ok |
| `currents` | `news` | `ok` | 1 | 30 | ok |
| `gnews` | `news` | `auth_error` | 1 | 0 | provider_payload_errors |
| `guardian` | `news` | `ok` | 1 | 1 | ok |
| `newsapi` | `news` | `ok` | 1 | 1 | ok |
| `newsdata` | `news` | `ok` | 1 | 1 | ok |
| `odds_api_io_events` | `odds` | `ok` | 1 | 10 | ok |
| `sportlogic` | `odds` | `rate_limited` | 1 | 0 | provider_payload_error |
| `allsportsapi` | `odds_context` | `ok` | 1 | 0 | ok |
| `sharpapi_configured_base` | `utility` | `missing_secret` | 0 | 0 | required secret is not configured |
| `meteostat` | `weather` | `ok` | 1 | 1 | ok |
| `openweathermap` | `weather` | `ok` | 1 | 0 | ok |
| `weatherapi` | `weather` | `ok` | 1 | 1 | ok |
