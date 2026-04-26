# V2 schedule and daily operations report

## Run timing

Observed runtime from workflow start to fallback: 7-10 minutes.

Therefore:

```yaml
- cron: '14,44 3-20 * * *'
```

This aims fallback at:

```text
:22-:24 for :00 kickoff cluster
:52-:54 for :30 kickoff cluster
```

## No-pick policy

No-pick Telegram reports are useful for scheduled runs, but noisy for manual/push runs.

Policy:

```text
schedule -> send no-pick report
workflow_dispatch/push -> do not send no-pick report
```

## Daily operations report

Runs:

```text
23:55 MSK current day
02:40 MSK previous day final settlement
```

Builder:

```bash
python scripts/build_daily_ops_report.py --send-telegram
```

Outputs:

```text
.data/exports/latest-daily-ops-report.json
.data/exports/latest-daily-ops-report.txt
.data/daily-ops-report-sent.json
```
