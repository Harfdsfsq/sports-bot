#!/usr/bin/env bash
set -euo pipefail

mkdir -p artifacts/run-bot
cp .logs/debug-last-run.json artifacts/run-bot/debug-last-run.json 2>/dev/null || true
cp artifacts/controlled-fallback-report.json artifacts/run-bot/controlled-fallback-report.json 2>/dev/null || true
cp .data/exports/latest-controlled-fallback-report.json artifacts/run-bot/latest-controlled-fallback-report.json 2>/dev/null || true
cp .data/exports/latest-rescue-candidates.json artifacts/run-bot/latest-rescue-candidates.json 2>/dev/null || true
cp .data/exports/latest-picks.json artifacts/run-bot/latest-picks.json 2>/dev/null || true
cp .data/exports/latest-quality-report.json artifacts/run-bot/latest-quality-report.json 2>/dev/null || true
cp .data/state.json artifacts/run-bot/state.json 2>/dev/null || true

python - <<'PY'
from pathlib import Path
import zipfile

bundle = Path("artifacts/run-bot-bundle.zip")
with zipfile.ZipFile(bundle, "w", zipfile.ZIP_DEFLATED) as z:
    for root in [Path("artifacts/run-bot"), Path(".data/exports")]:
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
