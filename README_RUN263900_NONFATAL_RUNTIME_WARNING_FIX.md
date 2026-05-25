# RUN 263900 non-fatal runtime warning fix

This patch fixes a false red `runtime_failed` v9 report.

The production run reached CandidateFactory/controlled fallback and ledger, but
`latest-run-bot.log` contained non-fatal discovery-first warnings:

`RuntimeError: asyncio.run() cannot be called from a running event loop`

Those warnings came from helper preparation scripts executed inside the async
`app.cli` phase. They should not be classified as a fatal runtime crash.

Changed files:
- `app/services/runtime_preflight.py`
  - `apply_phase_policy()` now applies env defaults and runtime extensions only.
  - It does not run the heavyweight discovery-first helper pipeline inside an active event loop.
- `scripts/send_harizon_telegram_run_report_v9.py`
  - runtime-error detection is now fatal-only.
  - It ignores helper warnings when fallback/candidate artifacts prove the pipeline evaluated candidates.
- `scripts/update_prediction_ledger.py`
  - does not add a `runtime_error` row for non-fatal helper warnings.
- `tests/test_run263900_nonfatal_runtime_warnings.py`

Publication guards are unchanged.
