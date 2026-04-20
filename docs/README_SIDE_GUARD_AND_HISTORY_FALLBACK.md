# Side guard and history fallback patch

This patch addresses the latest run issues:

- reject DNB / zero-handicap spread candidates when selected side clearly contradicts xG scenario;
- penalize single-source context more aggressively in quality scoring;
- allow market-derived candidates to pass on strong consensus even when history-ready signal is unavailable;
- build self-history fallback from `.data/state.json` when archived run history is too thin;
- expose inferred provider rate limits in run diagnostics.
