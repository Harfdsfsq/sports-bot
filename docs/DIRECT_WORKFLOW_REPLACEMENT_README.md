# Direct workflow replacement

This patch replaces workflow files directly because the logs showed old workflow/config still executing.

## Forecast workflow

Schedule:

```yaml
- cron: '14,44 3-20 * * *'
```

No-pick reports:

```text
schedule -> true
workflow_dispatch/push -> false
```

## Daily report workflow

Schedule:

```yaml
- cron: '55 20 * * *'
- cron: '40 23 * * *'
```

The second run reports previous MSK date and finalizes late settlements.

## Config patch

Run:

```bash
python scripts/apply_config_safe_relief_and_futrix_patch.py
```

It applies:

```text
CONTROLLED_FALLBACK_PROXY_SINGLE_SOURCE_MIN_CONFIDENCE=73.5
CONTROLLED_FALLBACK_PROXY_SINGLE_SOURCE_MIN_EDGE_PP=5.0
CONTROLLED_FALLBACK_PROXY_SINGLE_SOURCE_MIN_EV_PCT=10.0
FUTRIXMETRICS_PER_RUN_MAX=2
FUTRIXMETRICS_MIN_SPACING_MINUTES=15
```
