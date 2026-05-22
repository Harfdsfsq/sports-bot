# API Health Run

- Created UTC: `2026-05-22T17:52:57.255661+00:00`
- Mode: `quick`
- Providers checked: **18**
- OK: **14**
- Config-only/skipped: **16**
- Critical failures: **0**

## Provider results

| Provider | Group | Status | Requests | Useful rows | Message |
|---|---|---:|---:|---:|---|
| `bzzoiro` | `context` | `ok` | 1 | 50 | ok |
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
| `sportlogic` | `odds` | `skipped_daily_circuit` | 0 | 0 | SportLogic daily circuit is open; health check skipped before HTTP call. |
| `allsportsapi` | `odds_context` | `ok` | 1 | 1 | ok |
| `sharpapi_configured_base` | `utility` | `missing_secret` | 0 | 0 | required secret is not configured |
| `meteostat` | `weather` | `rate_limited` | 1 | 0 | http_status=429 |
| `openweathermap` | `weather` | `ok` | 1 | 0 | ok |
| `weatherapi` | `weather` | `ok` | 1 | 1 | ok |
