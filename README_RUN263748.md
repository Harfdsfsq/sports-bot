# Run 263748 follow-up: force v9 renderer + final CandidateFactory dedup

Observed in `run-bot-26374846305`:

- Publication was correctly blocked: `KI Klaksvik — AB Argir` failed because the under pick conflicted with xG, while the other raw candidate was negative after recalculation.
- The report was still v8 because workflow did not invoke `send_harizon_telegram_run_report_v9.py`.
- v8 incorrectly rendered `debug_candidates_before_quality` as a pre-evaluation filter even though fallback evaluated a candidate.
- CandidateFactory output dedup was not installed in the active runtime chain.

This patch:

1. Adds/updates `scripts/send_harizon_telegram_run_report_v9.py`.
2. Adds/updates `app/services/candidate_factory_output_dedup_patch.py`.
3. Patches `.github/workflows/run-bot.yml` to call v9 before v8.
4. Patches `app/services/runtime_startup_chain.py` to install dedup last.
5. Adds regression tests.

No publication thresholds are weakened.
