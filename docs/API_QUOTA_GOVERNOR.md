# Provider quota governor

This repository now uses `scripts/apply_provider_quota_governor.py` before `python -m app.cli run-once`.

The governor uses a conservative token-bucket model:

- every provider has a daily refill budget;
- unused tokens carry over up to `*_BUCKET_MAX`;
- each bot run can spend only `*_PER_RUN_MAX`;
- `*_RESERVE_TOKENS` protects the last part of the quota from being burned;
- all defaults are intentionally conservative and can be overridden from GitHub Actions variables/secrets or the runtime profile.

The defaults are not official provider free-tier limits. They are safe operating budgets chosen to avoid burning unknown free-plan quotas. If an official quota is known, set the provider-specific variables below.

## Main overrides

| Provider | Daily budget env | Bucket env | Per-run env |
|---|---|---|---|
| Odds API IO | `ODDS_API_IO_DAILY_BUDGET` | `ODDS_API_IO_BUCKET_MAX` | `ODDS_API_IO_PER_RUN_MAX` |
| OddsPAPI | `ODDSPAPI_DAILY_BUDGET` | `ODDSPAPI_BUCKET_MAX` | `ODDSPAPI_PER_RUN_MAX` |
| AllSportsAPI | `ALLSPORTSAPI_DAILY_BUDGET` | `ALLSPORTSAPI_BUCKET_MAX` | `ALLSPORTSAPI_PER_RUN_MAX` |
| SStats | `SSTATS_DAILY_BUDGET` | `SSTATS_BUCKET_MAX` | `SSTATS_PER_RUN_MAX` |
| Bzzoiro | `BZZOIRO_DAILY_BUDGET` | `BZZOIRO_BUCKET_MAX` | `BZZOIRO_PER_RUN_MAX` |
| API-Football | `API_FOOTBALL_DAILY_BUDGET` | `API_FOOTBALL_BUCKET_MAX` | `API_FOOTBALL_PER_RUN_MAX` |
| football-data.org | `FOOTBALL_DATA_DAILY_BUDGET` | `FOOTBALL_DATA_BUCKET_MAX` | `FOOTBALL_DATA_PER_RUN_MAX` |
| TheSportsDB | `THESPORTSDB_DAILY_BUDGET` | `THESPORTSDB_BUCKET_MAX` | `THESPORTSDB_PER_RUN_MAX` |
| FutrixMetrics | `FUTRIXMETRICS_DAILY_BUDGET` | `FUTRIXMETRICS_BUCKET_MAX` | `FUTRIXMETRICS_PER_RUN_MAX` |
| NewsAPI | `NEWSAPI_DAILY_BUDGET` | `NEWSAPI_BUCKET_MAX` | `NEWSAPI_PER_RUN_MAX` |
| GNews | `GNEWS_DAILY_BUDGET` | `GNEWS_BUCKET_MAX` | `GNEWS_PER_RUN_MAX` |
| Weather providers | `WEATHER_DAILY_BUDGET` | `WEATHER_BUCKET_MAX` | `WEATHER_PER_RUN_MAX` |
| RapidAPI Sportsbook | `RAPIDAPI_SPORTSBOOK_DAILY_BUDGET` | `RAPIDAPI_SPORTSBOOK_BUCKET_MAX` | `RAPIDAPI_SPORTSBOOK_PER_RUN_MAX` |
| RapidAPI Odds Feed | `RAPIDAPI_ODDS_FEED_DAILY_BUDGET` | `RAPIDAPI_ODDS_FEED_BUCKET_MAX` | `RAPIDAPI_ODDS_FEED_PER_RUN_MAX` |
| RapidAPI Free Football | `RAPIDAPI_FREE_FOOTBALL_DAILY_BUDGET` | `RAPIDAPI_FREE_FOOTBALL_BUCKET_MAX` | `RAPIDAPI_FREE_FOOTBALL_PER_RUN_MAX` |
| RapidAPI SportAPI7 | `RAPIDAPI_SPORTAPI7_DAILY_BUDGET` | `RAPIDAPI_SPORTAPI7_BUCKET_MAX` | `RAPIDAPI_SPORTAPI7_PER_RUN_MAX` |
| RapidAPI Meteostat | `RAPIDAPI_METEOSTAT_DAILY_BUDGET` | `RAPIDAPI_METEOSTAT_BUCKET_MAX` | `RAPIDAPI_METEOSTAT_PER_RUN_MAX` |

State is written to `.data/provider_quota_governor_state.json`; the latest export is written to `.data/exports/latest-provider-quota-governor.json`.

## Why this helps

The previous setup could call expensive providers on every run or probe RapidAPI until `daily_limit_reached`. The new setup limits every run and lets unused quota accumulate for busier fixture windows instead of spending the whole free tier early in the day.
