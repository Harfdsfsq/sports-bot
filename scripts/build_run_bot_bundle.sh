#!/usr/bin/env bash
set -euo pipefail

mkdir -p artifacts/run-bot
cp .logs/debug-last-run.json artifacts/run-bot/debug-last-run.json 2>/dev/null || true
cp .data/state.json artifacts/run-bot/state.json 2>/dev/null || true
cp .data/exports/latest-picks.json artifacts/run-bot/latest-picks.json 2>/dev/null || true
cp .data/exports/latest-quality-report.json artifacts/run-bot/latest-quality-report.json 2>/dev/null || true
cp .data/exports/latest-controlled-fallback-report.json artifacts/run-bot/latest-controlled-fallback-report.json 2>/dev/null || true
cp artifacts/controlled-fallback-report.json artifacts/run-bot/controlled-fallback-report.json 2>/dev/null || true

python - <<'PY'
import json
from pathlib import Path
payload = {}
for name in ("debug-last-run", "latest-controlled-fallback-report", "latest-quality-report"):
    for path in (Path("artifacts/run-bot") / f"{name}.json", Path(".data/exports") / f"{name}.json"):
        if path.exists():
            try:
                payload[name] = json.loads(path.read_text(encoding="utf-8"))
                break
            except Exception:
                pass
Path("artifacts/run-bot/run-bot-summary.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
PY

rm -f artifacts/run-bot-bundle.zip
python - <<'PY'
from pathlib import Path
import zipfile
root = Path("artifacts/run-bot")
with zipfile.ZipFile("artifacts/run-bot-bundle.zip", "w", compression=zipfile.ZIP_DEFLATED) as z:
    for path in root.rglob("*"):
        if path.is_file():
            z.write(path, path.relative_to("artifacts"))
PY
