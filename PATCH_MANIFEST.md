# run263735 dedup + report v9 fix

Fixes for the 2026-05-25 00:59 run:

- CandidateFactory output is de-duplicated by match+market+selection+line so the
  same Cavalier FC candidate is not counted twice.
- v9 report is used before v8 in run-bot workflow.
- v9 report keeps true pre-fallback filters such as
  `*_canonical_negative_value_prefilter`, but ignores pure source-pool counters.
- v9 conclusion now says the value safety gate worked when candidates are filtered
  by negative post-calibration/canonical value before fallback.

Publication rules are not loosened.
