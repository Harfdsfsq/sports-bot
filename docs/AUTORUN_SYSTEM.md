# HARIZON autorun system

## Goal

The bot must run close to the planned 2-hour slots and must not miss matches when GitHub scheduled workflows are delayed or skipped.

The new system uses three layers:

1. **Primary schedule**: `.github/workflows/run-bot.yml` still runs every 2 hours.
2. **Internal supervisor**: `.github/workflows/autorun-supervisor.yml` runs every 5 minutes and dispatches `run-bot.yml` when the expected slot is missing or stale.
3. **External watchdog-ready trigger**: any external cron/webhook service can call GitHub `repository_dispatch` and wake the supervisor independently of GitHub's own cron timing.

The supervisor is stateful through `.data/autorun-state.json`, which is already persisted by `scripts/sync_persistent_state.py`. Duplicate protection remains in `scripts/autorun_gate.py`.

---

## Runtime slots

Slots are 2-hour local MSK slots:

```text
00:00, 02:00, 04:00, 06:00, 08:00, 10:00, 12:00, 14:00, 16:00, 18:00, 20:00, 22:00 MSK
```

`run-bot.yml` uses UTC cron:

```yaml
0 21,23,1,3,5,7,9,11,13,15,17,19 * * *
```

That maps to the MSK slots above.

---

## Supervisor behavior

`autorun-supervisor.yml` runs every 5 minutes.

Default policy:

```text
AUTORUN_SUPERVISOR_DELAY_MINUTES=7
AUTORUN_PENDING_TTL_MINUTES=70
AUTORUN_MAX_CATCHUP_SLOTS=4
```

Meaning:

- The supervisor waits 7 minutes after a planned slot.
- If the slot is already `success` or `recovered`, it does nothing.
- If the slot is `running`/`dispatched` and younger than 70 minutes, it does nothing.
- If the slot is missing, failed, or stale, it dispatches `run-bot.yml` with `run_reason=watchdog_recovery`.
- If multiple slots are missed, it dispatches catchup with `run_reason=catchup` and sends `missed_slots`, `catchup_from`, `catchup_to`.

---

## Manual supervisor run

GitHub UI:

```text
Actions → autorun-supervisor → Run workflow
```

Useful options:

- `force=true`: force a supervisor check now.
- `slot_key=2026-05-03T14:00:00+03:00`: force a specific slot.
- `mode=dry_run`: generate supervisor report without dispatching `run-bot`.

---

## External watchdog setup

External services you can use:

- cron-job.org
- UptimeRobot webhook monitor
- Better Stack Heartbeats
- any VPS with cron + curl
- Pipedream / Make / Zapier webhook job

Recommended: **cron-job.org every 5 minutes**.

### 1. Create GitHub fine-grained token

Create a fine-grained GitHub token for this repository with permissions:

```text
Repository permissions:
- Actions: Read and write
- Contents: Read and write
- Metadata: Read-only
```

The token owner must have access to `Harfdsfsq/sports-bot`.

### 2. External POST request

Endpoint:

```text
POST https://api.github.com/repos/Harfdsfsq/sports-bot/dispatches
```

Headers:

```text
Accept: application/vnd.github+json
Authorization: Bearer <YOUR_GITHUB_TOKEN>
Content-Type: application/json
X-GitHub-Api-Version: 2022-11-28
```

Body:

```json
{
  "event_type": "external-autorun-watchdog",
  "client_payload": {
    "force": "false"
  }
}
```

This wakes `autorun-supervisor.yml`. The supervisor then decides whether `run-bot.yml` actually needs to start. This avoids duplicates.

### 3. Force a specific slot externally

Use only for manual recovery:

```json
{
  "event_type": "external-autorun-watchdog",
  "client_payload": {
    "force": "true",
    "slot_key": "2026-05-03T14:00:00+03:00"
  }
}
```

---

## Why external watchdog helps

GitHub Actions schedule is best-effort. It can be delayed or skipped under load. The external service does not run the bot directly; it wakes the supervisor. The supervisor checks persistent slot state and only dispatches `run-bot` if needed.

This gives:

- fewer missed slots;
- no duplicate picks from repeated webhook calls;
- catchup after stale/failed runs;
- a visible audit file: `.data/exports/latest-autorun-supervisor.json`.

---

## Files involved

```text
.github/workflows/run-bot.yml
.github/workflows/autorun-supervisor.yml
scripts/autorun_gate.py
scripts/autorun_supervisor.py
scripts/autorun_state.py
scripts/sync_persistent_state.py
.data/autorun-state.json
.data/exports/latest-autorun-supervisor.json
.data/exports/latest-autorun-state.json
```

---

## Health check checklist

After the next supervisor run, check:

```text
.data/exports/latest-autorun-supervisor.json
```

Expected no-action state:

```json
{
  "status": "noop",
  "reason": "current_slot_already_completed"
}
```

Expected recovery state:

```json
{
  "status": "dispatched",
  "reason": "watchdog_recovery",
  "target_slot_key": "..."
}
```

If dispatch fails with `github_token_missing`, check that workflow has:

```yaml
permissions:
  actions: write
```

or set repository secret:

```text
AUTORUN_DISPATCH_TOKEN
```

with Actions read/write permission.
