# Failed run 43196472202: runtime key collision and optional enrichment

The 13:00 Moscow run of SHA `91132ff` failed with
`KeyError: 'sstats:context'` in the optional cached standings update. Its final
summary is an error receipt, not valid coverage or model-validation output.
The workflow correctly ended red after saving diagnostics and state. Do not
report the previous run's metrics as current or count this as a completed trial.

The saved registry contains five legacy runtime-key collisions: the same teams
on the same day, with different kickoffs. `match_identity` distinguishes them,
but provider result dictionaries still use the day-level `match_key`. For
example, Tanganyika/AS New Soger appears at 10:30 and 11:00 UTC, and Independiente
Petrolero/Always Ready appears at 22:00 and 23:00 UTC on the previous day. One
record may have SStats evidence while the other does not. A day-keyed context
from the first record was therefore reused while updating the second, causing
the missing-key exception. Anges Verts/Étoile du Congo, Céleste/Maniema Union
and LIU Sharks/Mercyhurst also have conflicting kickoff records.

## Repair

- Keep all registry records and expose conflicts explicitly. Exclude ambiguous
  runtime keys from collection queues, candidate inventory, provider cache maps
  and closing-price watches. Never guess which kickoff is correct or transfer
  evidence to the other record. Once an authoritative correction removes the
  conflict, the unique record can be used without moving its receipt.
- Retain conflicting fixtures in coverage denominators. They do not count as
  ready, and `runtime_identity_quarantined` plus
  `near_runtime_identity_quarantined` expose the data limitation.
- Enrich detached copies of cached form. After awaiting standings, re-read and
  revalidate the exact registry receipt. A removed or expired receipt stays
  unusable; a newer receipt is retained without the old table overwriting it.
  A table update never changes the form observation timestamp.
- Export `latest-runtime-identity-conflicts.json` and flag current-window
  conflicts in the system-readiness report. No validation protocol or model
  mathematics changed; the stable cohort is preserved.

Regression cases cover conflicting day keys, preserved inventory, no evidence
transfer, unchanged form timestamps, cache removal, expiry and replacement
during an async standings request. The isolated native suite passed 675 tests;
Ruff and changed-module mypy passed. Profitability and coverage recovery still
require a successful new run; neither is inferred from passing unit tests.

Full local suite: 97 failed / 1,089 passed; all failure IDs match the previous
local baseline. An unrelated cache-reuse test now freezes its request clock:
two calls are required to reuse a cache only when their date-time query is
identical. Its existing assertions are unchanged; this removes second-boundary
flakiness without changing the Bzzoiro implementation.
