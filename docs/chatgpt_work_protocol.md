# ChatGPT work protocol for Harizon sports-bot

This document captures the required workflow for repository-related work in this project.

## Required order of work

For every user request about logs, bot behavior, API sources, forecasts, quality, workflow runs, or repository changes:

1. Analyze the user's message and any files uploaded in the conversation first.
2. Analyze the repository state, relevant source files, workflows, config, reports, and recent logs available in the repo.
3. Produce a concrete correction/improvement plan before making changes when the task is non-trivial.
4. Implement the changes in the repository, preferably on the active fix branch unless the user explicitly requests another branch.
5. Keep quality gates intact unless the user explicitly asks to change them; prefer fixing data coverage, provider wiring, matching, diagnostics, and reporting before relaxing thresholds.
6. After changes, summarize what was changed, what remains to test, and which secrets/env keys are required.

## Current project-specific constraints

- `api-football` is intentionally removed from runtime and should not be restored without an explicit user request.
- SportLogic is the new provider and uses `SPORTLOGIC_API_KEY` as the preferred secret/env key.
- Do not publish duplicate predictions only to increase count; duplicate guards are intentional.
- When reports disagree with actual runtime counters, fix inventory/report merge logic rather than lowering model quality.
