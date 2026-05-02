#!/usr/bin/env bash
set -euo pipefail

mkdir -p artifacts/run-bot

# Post-run analysis hooks. These are intentionally non-fatal: they should enrich
# the run bundle and Telegram diagnostics, not break prediction publication.
python scripts/build_match_analysis_audit.py || true
python scripts/update_daily_candidate_pool.py || true
python scripts/send_daily_best5_no_pick_note.py || true
python scripts/send_completed_daily_report_when_closed.py || true
python scripts/sportlogic_probe.py || true

cp .logs/debug-last-run.json artifacts/run-bot/debug-last-run.json 2>/dev/null || true
cp artifacts/controlled-fallback-report.json artifacts/run-bot/controlled-fallback-report.json 2>/dev/null || true
cp .data/exports/latest-controlled-fallback-report.json artifacts/run-bot/latest-controlled-fallback-report.json 2>/dev/null || true
cp .data/exports/latest-rescue-candidates.json artifacts/run-bot/latest-rescue-candidates.json 2>/dev/null || true
cp .data/exports/latest-picks.json artifacts/run-bot/latest-picks.json 2>/dev/null || true
cp .data/exports/latest-quality-report.json artifacts/run-bot/latest-quality-report.json 2>/dev/null || true
cp .data/exports/latest-match-analysis-audit.json artifacts/run-bot/latest-match-analysis-audit.json 2>/dev/null || true
cp .data/exports/latest-daily-candidate-pool.json artifacts/run-bot/latest-daily-candidate-pool.json 2>/dev/null || true
cp .data/exports/latest-completed-daily-report-check.json artifacts/run-bot/latest-completed-daily-report-check.json 2>/dev/null || true
cp .data/exports/latest-daily-best5-no-pick-note.json artifacts/run-bot/latest-daily-best5-no-pick-note.json 2>/dev/null || true
cp .data/exports/latest-sportlogic-probe.json artifacts/run-bot/latest-sportlogic-probe.json 2>/dev/null || true
cp .data/exports/latest-sportlogic-debug.json artifacts/run-bot/latest-sportlogic-debug.json 2>/dev/null || true
cp .data/exports/latest-sportlogic-odds-sample.json artifacts/run-bot/latest-sportlogic-odds-sample.json 2>/dev/null || true
cp .data/state.json artifacts/run-bot/state.json 2>/dev/null || true

python - <<'PY'
from pathlib import Path
import zipfile

bundle = Path("artifacts/run-bot-bundle.zip")
with zipfile.ZipFile(bundle, "w", zipfile.ZIP_DEFLATED) as z:
    for root in [Path("artifacts/run-bot"), Path(".data/exports"), Path(".data/day_candidates")]:
        if not root.exists():
            continue
        for p in root.rglob("*"):
            if p.is_file():
                z.write(p, p.as_posix())
    for p in [Path("artifacts/controlled-fallback-report.json"), Path(".logs/debug-last-run.json"), Path(".data/state.json")]:
        if p.exists():
            z.write(p, p.as_posix())
print(f"Built {bundle}")
PY
