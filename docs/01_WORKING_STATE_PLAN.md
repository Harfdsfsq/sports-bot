# Working-state plan from the resume and the latest runs

## Goal
Move the bot from “sometimes publishes a bet” to “publishes fewer but cleaner bets daily”.

## What the resume already identified
1. Filters were too strict and often killed all candidates.
2. Probability calibration was weak.
3. The model leaned too much on xG and external providers without enough structural controls.
4. Bankroll logic needed tightening.
5. The rollout should be phased with logging, backtest and monitoring.

## What the latest runs added
The main failure mode is no longer only “empty output”.
The newer risk is **bad-profile publishes**:
- single-source
- heavy-shrink
- non-core or cup matches
- high odds with small xG edge

## Practical target state
- Every day the pipeline runs on schedule.
- Main channel publishes only tier-A or tier-B candidates.
- Tier-C candidates go to shadow or dry-run only.
- Emergency publish and historical relief do not leak weak bets into the main channel.
- Calibration clips aggressive gaps to market.
- Bankroll keeps drawdown controlled.

## Rollout order
### Stage 1 — Stabilize publication
Use `profit_core_daily.env`.
Disable emergency and historical relief.
Cap odds and force 2-book confirmation.

### Stage 2 — Collect clean evidence
Run `research_shadow` manually when you need more coverage.
Do not use shadow output as Telegram output.

### Stage 3 — Promote only proven segments
After 80-120 settled bets, update `.data/calibration-profile.json`.
Promote only segments with positive CLV and acceptable hit-rate / ROI.

### Stage 4 — Add model features
Only after publication quality becomes stable:
- injuries
- squad rotation
- cup/league context separation
- table strength / schedule fatigue
- market movement speed and closing-line value
