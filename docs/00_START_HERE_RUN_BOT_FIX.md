# Run bot fix — latest logs/code pass

## What this patch fixes

This package keeps the normal **Run bot** workflow as the only operational entrypoint. It adds a robust integrity audit and a stable `run-bot-current` artifact after every run.

## Why this patch exists

The latest run was technically healthy:

- `odds_api_io` key was present;
- 82 matches were inside the 24h window;
- 53 matches had offers;
- 49 contexts were built;
- 4 candidates reached quality.

No pick was published because all 4 candidates failed quality. That was the correct decision: the historical profile is still negative and the candidate set contained high-odds / post-calibration / low-score cases.

## Important behavior change

The balanced profile is now safer:

- no emergency publish;
- no historical relief publish;
- no last-resort publish;
- max odds reduced;
- only 1 pick max per run;
- market-derived candidates disabled by default because they mostly create noisy guard counts in current logs.

## Artifact to upload for analysis

After GitHub Actions finishes, download artifact:

`run-bot-current`

Inside it, upload:

`artifacts/run-bot-bundle.zip`

## Files changed

- `.github/workflows/run-bot.yml`
- `app/services/candidate_integrity.py`
- `scripts/audit_candidate_integrity.py`
- `scripts/summarize_run_bot.py`
- `scripts/build_run_bot_bundle.py`
- `config/balanced_output.env`
- `config/conservative_passability.env`
- `config/calibration-profile.example.json`
