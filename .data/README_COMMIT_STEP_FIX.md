# Fix for GitHub Actions post-run commit step

## Problem
The workflow failed after the bot run because the `git add` command referenced files that were not created in that specific run:
- `.data/sheet-run-summary.json`
- `.data/sheet-picks.csv`
- `.data/sheet-matches.csv`

When `git add` receives a missing pathspec, the step can fail.

## Safe fix
Replace the explicit file list with:

```bash
git add -A .data
```

This stages new, modified, and deleted files only inside `.data`, without failing when some optional artifacts are absent.

## Recommended shell block

```bash
git config user.name "github-actions"
git config user.email "github-actions@github.com"
git add -A .data
git diff --cached --quiet && exit 0
git commit -m "Update bot state"
git push
```

Your latest log shows the bot step itself completed, and the failure happened only on the commit step with:
`fatal: pathspec '.data/sheet-run-summary.json' did not match any files`
