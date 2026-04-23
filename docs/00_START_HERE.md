# HARIZON observability overlay

## Goal

Make the bot easier to debug, audit and improve without guessing.

The current state layer already stores:
- `last_run`
- archived run payloads
- `run_history`
- `message_history`
- `learning_state`
- prediction ledger / daily reports / bankroll state

That is visible directly in `app/state.py` and is the right foundation for further improvements. fileciteturn46file0

## What this overlay adds

- a **learning bundle** generator after runs
- a manual GitHub Actions workflow to export the latest diagnostics
- a compact latest-run summary
- an env profile that keeps richer traces enabled
- docs for what to upload next time for analysis

## Fast start

1. Unpack this overlay into the repository root.
2. Copy values from `config/observability_capture.env` into your active `.env` or GitHub Actions env.
3. Run **Run bot • observability** or **Ops • Learning bundle** in GitHub Actions.
4. Download the artifact `learning-bundle-...zip`.
5. Share that zip in chat next time when you want deep analysis.
