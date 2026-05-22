# sports-bot run263122 post-integrity rescue rewrap fix

## Problem
The run had 1078 matches, 352 with odds and 218 with context, but `raw_candidates=0`.
`bzzoiro_exact_offer_bridge` found 30 likely allowed 2-source buckets, yet
`latest-post-integrity-candidate-rescue.json` only contained the installer marker
`status=installed`. The rescue wrapper was not present in the active
`CandidateFactory.build_candidates` callable.

Root cause: `post_integrity_candidate_rescue.install()` trusted the class-level
flag `CandidateFactory._harizon_post_integrity_candidate_rescue_patch`. Later
final/reinstall wrappers can replace `build_candidates`, leaving the class flag
true while the current callable no longer includes the rescue wrapper.

## Fix
`post_integrity_candidate_rescue.install()` now checks the marker on the current
`CandidateFactory.build_candidates` callable. If the class flag is stale but the
current callable is unwrapped, it rewraps the active chain and writes execution
artifacts (`pass_through`, `no_candidate`, or `rescued`) during the real run.

## Safety
This does not loosen Telegram publication. It only restores the intended
pre-fallback discovery bridge so controlled fallback can evaluate candidates
through existing EV/edge/source/xG/quality guards.
