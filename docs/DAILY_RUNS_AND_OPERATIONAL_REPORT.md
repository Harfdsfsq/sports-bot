# Daily runs and operational report

## Forecast runs

Forecast workflow should start before common kickoff slots.

Recommended schedule:

```yaml
- cron: '2,32 3-20 * * *' # 06:02-23:32 MSK
```

Reason:

- `MIN_KICKOFF_LEAD_MINUTES=30` stays strict.
- The run starts before `:00/:30` kickoff clusters.
- Fallback is less likely to run at `:31` for a `:00` match.

## Daily reports

Two daily report slots:

```yaml
- cron: '55 20 * * *' # 23:55 MSK, current local day
- cron: '40 23 * * *' # 02:40 MSK, previous local day finalizer
```

## Report contents

The operational report includes:

- run count and errors;
- matches seen;
- matches with odds;
- contexts built;
- candidates and publishable candidates;
- published forecasts;
- settled forecasts;
- pending forecasts;
- bankroll/open exposure/PnL;
- provider quota grants;
- top fallback reject reasons.

## Settlement

The report workflow still runs:

```bash
python -m app.cli run-once
```

with:

```env
SETTLEMENT_ENABLED=true
SETTLEMENT_GRACE_MINUTES=180
SETTLEMENT_LOOKBACK_DAYS=7
```

So before the report is sent, pending bets are checked and closed when settlement data is available.

## Duplicate protection

`build_daily_ops_report.py` writes:

```text
.data/daily-ops-report-sent.json
```

It stores hash by report date. If the report is unchanged, Telegram is not spammed.
