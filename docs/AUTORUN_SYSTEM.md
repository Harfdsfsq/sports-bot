# HARIZON autorun system

## Goal

The bot must run close to the planned 2-hour slots and must not duplicate runs when GitHub scheduled workflows are delayed.

The current system uses a **single scheduler authority**:

1. **Supervisor schedule**: `.github/workflows/autorun-supervisor.yml` wakes every 5 minutes on GitHub cron.
2. **External cron wake-up**: cron-job.org or another external cron can wake the supervisor every 1 minute through `repository_dispatch`.
3. **Bot run**: `.github/workflows/run-bot.yml` has no direct cron. It runs only through `workflow_dispatch`, normally dispatched by the supervisor.

This avoids the old duplicate pattern:

```text
run-bot GitHub cron is late → supervisor dispatches recovery → delayed run-bot cron starts too
```

With supervisor-only scheduling, delayed GitHub schedule events can only delay/duplicate the supervisor check, not the bot run itself.

---

## Runtime slots

Slots are 2-hour local MSK slots:

```text
00:00, 02:00, 04:00, 06:00, 08:00, 10:00, 12:00, 14:00, 16:00, 18:00, 20:00, 22:00 MSK
```

The supervisor determines the latest expected slot and dispatches `run-bot.yml` when that slot is missing, failed, or stale.

---

## Workflows

### `.github/workflows/run-bot.yml`

`run-bot.yml` is dispatch-only:

```yaml
on:
  workflow_dispatch:
```

It intentionally has no `schedule:` block.

### `.github/workflows/autorun-supervisor.yml`

The supervisor is the only scheduled GitHub workflow:

```yaml
schedule:
  - cron: "*/5 * * * *"
```

Recommended runtime policy:

```text
AUTORUN_SUPERVISOR_DELAY_MINUTES=2
AUTORUN_PENDING_TTL_MINUTES=90
AUTORUN_MAX_CATCHUP_SLOTS=2
AUTORUN_SUPERVISOR_MAX_DISPATCHES=1
```

Meaning:

- The supervisor waits 2 minutes after a planned slot.
- If the slot is already `success` or `recovered`, it does nothing.
- If the slot is `running`/`dispatched` and younger than 90 minutes, it does nothing.
- If the slot is missing, failed, or stale, it dispatches `run-bot.yml`.
- It dispatches at most one bot run per supervisor check.

---

## External cron-job.org setup

Recommended: **cron-job.org every 1 minute**.

The external cron should not call `run-bot.yml` directly. It must only wake the supervisor.

### 1. Create GitHub fine-grained token

Create a fine-grained GitHub token for `Harfdsfsq/sports-bot` with:

```text
Repository permissions:
- Actions: Read and write
- Contents: Read and write
- Metadata: Read-only
```

### 2. cron-job.org request

Method:

```text
POST
```

URL:

```text
https://api.github.com/repos/Harfdsfsq/sports-bot/dispatches
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

The supervisor will decide whether a bot run is actually needed. Repeated external cron calls are safe because slot state prevents duplicates.

---

## Manual checks

Dry-run supervisor:

```text
Actions → autorun-supervisor → Run workflow → mode=dry_run
```

Force supervisor check:

```text
Actions → autorun-supervisor → Run workflow → force=true
```

Force a specific slot only when manually recovering:

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

## Expected reports

No action:

```json
{
  "status": "noop",
  "reason": "slot_completed"
}
```

Recovery dispatch:

```json
{
  "status": "dispatched",
  "target_slot_key": "..."
}
```

Duplicate guard in run-bot:

```json
{
  "AUTORUN_SKIP_MAIN": "true",
  "AUTORUN_DECISION_REASON": "skip_slot_already_completed"
}
```

Direct schedule guard, if a schedule trigger is accidentally reintroduced:

```json
{
  "AUTORUN_SKIP_MAIN": "true",
  "AUTORUN_DECISION_REASON": "skip_direct_schedule_disabled_supervisor_only"
}
```

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
.data/exports/latest-autorun-gate.json
.data/exports/latest-autorun-state.json
```

---

## Health checklist

After the next slot, check:

```text
.data/exports/latest-autorun-supervisor.json
.data/exports/latest-autorun-gate.json
```

Healthy behavior:

- one `run-bot` dispatch per 2-hour slot;
- no direct scheduled `run-bot` events;
- supervisor checks can be frequent;
- repeated supervisor wake-ups produce `noop` after a slot is completed or pending.
