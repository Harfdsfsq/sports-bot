# Controlled fallback fix for normal Run bot

This patch keeps the ordinary **Run bot** workflow as the main execution path.

It does not blindly weaken the quality layer. Instead, after the normal bot run:

1. If normal `latest-picks.json` contains a pick, nothing extra is published.
2. If normal quality returns zero picks, `scripts/publish_controlled_fallback.py` inspects `candidates_before_quality` from `.logs/debug-last-run.json`.
3. It can publish exactly one low-stake fallback pick only if the candidate passes a strict second gate:
   - allowed family: `totals` or `dnb`
   - odds range: default `1.65–2.85`
   - books: at least `2`
   - confidence: at least `60`
   - quality score: at least `68`
   - canonical edge from selected odds: at least `+3.0 pp`
   - canonical EV from selected odds: at least `+5.0%`
   - allowed quality stops only: historical/no-bet/post-calibration-probability
   - stake capped to a small amount

## Why this fix

The latest run had candidates, but all were rejected by quality. The best candidate was a totals under with positive canonical EV and two bookmakers, but it was blocked by historical guard. This patch allows one carefully controlled low-stake forecast in that situation.

## How to apply

1. Unzip into repository root.
2. Check diff in GitHub Desktop.
3. Commit and push.
4. Run **Run bot** with profile `balanced`.
5. Upload `run-bot-current` artifact after the run.

## Important

This is not a profit guarantee. It is a controlled way to avoid zero-output days while keeping the main quality layer intact.
