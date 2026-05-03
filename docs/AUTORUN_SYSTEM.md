# HARIZON autorun system

## Goal

The bot must run exactly from an external cron provider every 2 hours, without GitHub schedule delays and without duplicate bot runs.

Current production mode:

1. **cron-job.org** triggers `.github/workflows/run-bot.yml` directly through GitHub workflow dispatch every 2 hours.
2. **`run-bot.yml` has no GitHub `schedule:` block.**
3. **`autorun-supervisor.yml` has no GitHub `schedule:` block.** It remains available only for manual/recovery diagnostics.
4. `scripts/autorun_gate.py` still prevents duplicate execution for an already completed slot.

This removes the old duplicate pattern:

```text
GitHub run-bot cron is delayed → watchdog dispatches recovery → delayed run-bot cron starts too
```

There is now only one regular scheduler: the external cron job.

---

## Runtime slots

Use 2-hour MSK slots:

```text
00:00, 02:00, 04:00, 06:00, 08:00, 10:00, 12:00, 14:00, 16:00, 18:00, 20:00, 22:00 MSK
```

cron-job.org should call the GitHub workflow dispatch endpoint at these times.

---

## cron-job.org setup

Recommended: create one cron-job.org job that runs every 2 hours.

### Method

```text
POST
```

### URL

```text
https://api.github.com/repos/Harfdsfsq/sports-bot/actions/workflows/run-bot.yml/dispatches
```

### Headers

```text
Accept: application/vnd.github+json
Authorization: Bearer <YOUR_GITHUB_TOKEN>
Content-Type: application/json
X-GitHub-Api-Version: 2022-11-28
```

### Body

```json
{
  "ref": "main",
  "inputs": {
    "run_reason": "external_watchdog",
    "profile": "balanced",
    "publish_window_hours": "9",
    "min_kickoff_lead_minutes": "25",
    "send_detailed_report": "true",
    "volume_mode": "target_5"
  }
}
```

Do not send `slot_key` from cron-job.org. The bot calculates the current slot internally.

---

## GitHub token permissions

Create a fine-grained GitHub token for `Harfdsfsq/sports-bot` with:

```text
Repository permissions:
- Actions: Read and write
- Contents: Read and write
- Metadata: Read-only
```

The token owner must have access to the repository.

---

## Workflows

### `.github/workflows/run-bot.yml`

Production trigger:

```yaml
on:
  workflow_dispatch:
```

No `schedule:` block.

### `.github/workflows/autorun-supervisor.yml`

Manual/recovery only:

```yaml
on:
  workflow_dispatch:
  repository_dispatch:
```

No `schedule:` block.

---

## Duplicate protection

`run-bot.yml` uses:

```yaml
concurrency:
  group: run-bot-main
  cancel-in-progress: false
```

and `scripts/autorun_gate.py` checks slot state before executing the main run.

Expected duplicate skip:

```json
{
  "AUTORUN_SKIP_MAIN": "true",
  "AUTORUN_DECISION_REASON": "skip_slot_already_completed"
}
```

If a GitHub schedule trigger is accidentally reintroduced into `run-bot.yml`, the gate blocks it by default:

```json
{
  "AUTORUN_SKIP_MAIN": "true",
  "AUTORUN_DECISION_REASON": "skip_direct_schedule_disabled_supervisor_only"
}
```

---

## Manual recovery

Manual run:

```text
Actions → run-bot → Run workflow
```

Use this for operator probes after code/config changes. Manual runs without `slot_key` are treated as manual probes and do not overwrite scheduled slot state.

Manual recovery for a real missed slot:

```text
Actions → run-bot → Run workflow
slot_key=2026-05-03T14:00:00+03:00
run_reason=external_watchdog
```

---

## Health checklist

After the next external cron run, check:

```text
.data/exports/latest-autorun-gate.json
.data/exports/latest-run-summary.json
```

Healthy behavior:

- one `run-bot` workflow every 2 hours;
- no scheduled `run-bot` events from GitHub;
- no scheduled `autorun-supervisor` events from GitHub;
- duplicate external calls for the same slot are skipped by `autorun_gate.py`.
