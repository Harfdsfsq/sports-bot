#!/usr/bin/env bash
set -euo pipefail

mkdir -p artifacts/run-bot

copy_if_exists() {
  local src="$1"
  local dst="$2"
  if [ -e "$src" ]; then
    cp -R "$src" "$dst"
  fi
}

copy_if_exists ".logs/debug-last-run.json" "artifacts/run-bot/debug-last-run.json"
copy_if_exists ".data/state.json" "artifacts/run-bot/state.json"
copy_if_exists ".data/exports/latest-picks.json" "artifacts/run-bot/latest-picks.json"
copy_if_exists ".data/exports/latest-picks.csv" "artifacts/run-bot/latest-picks.csv"
copy_if_exists ".data/exports/latest-bets.json" "artifacts/run-bot/latest-bets.json"
copy_if_exists ".data/exports/latest-matches.json" "artifacts/run-bot/latest-matches.json"
copy_if_exists ".data/exports/latest-quality-report.json" "artifacts/run-bot/latest-quality-report.json"
copy_if_exists ".data/exports/latest-controlled-fallback-report.json" "artifacts/run-bot/latest-controlled-fallback-report.json"
copy_if_exists ".data/exports/latest-controlled-fallback-pick.json" "artifacts/run-bot/latest-controlled-fallback-pick.json"
copy_if_exists "artifacts/controlled-fallback-report.json" "artifacts/run-bot/controlled-fallback-report.json"

python - <<'PY'
import json
from pathlib import Path
from datetime import datetime, timezone

debug = {}
try:
    debug = json.loads(Path(".logs/debug-last-run.json").read_text(encoding="utf-8"))
except Exception:
    debug = {}

fallback = {}
try:
    fallback = json.loads(Path("artifacts/controlled-fallback-report.json").read_text(encoding="utf-8"))
except Exception:
    pass

summary = dict(debug.get("summary") or {}) if isinstance(debug, dict) else {}
payload = {
    "created_at": datetime.now(timezone.utc).isoformat(),
    "run_summary": summary,
    "fallback_status": fallback.get("status"),
    "fallback_published": fallback.get("published"),
    "fallback_selected": fallback.get("selected"),
    "candidates_before_quality": summary.get("candidates_before_quality") or summary.get("candidates"),
    "candidates_after_quality": summary.get("candidates_after_quality"),
    "published": summary.get("published"),
    "top_rejections": summary.get("rejections") or summary.get("top_rejections") or {},
}
Path("artifacts/run-bot/latest-run-summary.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
PY

python - <<'PY'
from pathlib import Path
import zipfile

target = Path("artifacts/run-bot-bundle.zip")
target.parent.mkdir(parents=True, exist_ok=True)
with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
    for path in Path("artifacts/run-bot").rglob("*"):
        if path.is_file():
            zf.write(path, path.as_posix())
print(f"Wrote {target}")
PY
