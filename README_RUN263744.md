# run263744 v9/dedup v2 follow-up

Fresh run still used v8 and did not install CandidateFactory output dedup.
This package makes the previous fix effective in `run-bot`:

- call `send_harizon_telegram_run_report_v9.py` before v8 in workflow;
- install `candidate_factory_output_dedup_patch` before final diagnostics;
- improve v9 so when fallback actually evaluated candidates, stale/source pool counters are not rendered as pre-evaluation filters.

Expected next run:

- Telegram header: `HARIZON run report v9`;
- no misleading `Pre-evaluation filters: debug candidates before quality` when `fallback seen/evaluated > 0`;
- duplicated logical candidates collapse before diagnostics;
- publication rules are unchanged.
