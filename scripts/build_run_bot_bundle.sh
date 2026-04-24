#!/usr/bin/env bash
set -euo pipefail

mkdir -p artifacts/run-bot

copy_if_exists() {
  local src="$1"
  local dst="$2"
  if [ -e "$src" ]; then
    mkdir -p "$(dirname "$dst")"
    cp -R "$src" "$dst"
  fi
}

copy_if_exists ".logs/debug-last-run.json" "artifacts/run-bot/debug-last-run.json"
copy_if_exists ".data/state.json" "artifacts/run-bot/state.json"
copy_if_exists "artifacts/controlled-fallback-report.json" "artifacts/run-bot/controlled-fallback-report.json"
copy_if_exists ".data/fallback-sent-index.json" "artifacts/run-bot/fallback-sent-index.json"

if [ -d ".data/exports" ]; then
  mkdir -p artifacts/run-bot/exports
  find .data/exports -maxdepth 2 -type f \( -name "latest-*.json" -o -name "latest-*.csv" \) -print0 \
    | while IFS= read -r -d '' file; do
        rel="${file#.data/exports/}"
        mkdir -p "artifacts/run-bot/exports/$(dirname "$rel")"
        cp "$file" "artifacts/run-bot/exports/$rel"
      done
fi

if [ -d ".logs/runs" ]; then
  mkdir -p artifacts/run-bot/runs
  find .logs/runs -type f -name "*-run.json" | sort | tail -n 5 | while read -r file; do
    cp "$file" "artifacts/run-bot/runs/$(basename "$file")"
  done
fi

python - <<'PY'
import json
from pathlib import Path
summary = {
    "created_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
    "files": []
}
root = Path("artifacts/run-bot")
for p in sorted(root.rglob("*")):
    if p.is_file():
        summary["files"].append(str(p))
Path("artifacts/run-bot/manifest.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
PY

rm -f artifacts/run-bot-bundle.zip
python - <<'PY'
import zipfile
from pathlib import Path
with zipfile.ZipFile("artifacts/run-bot-bundle.zip", "w", compression=zipfile.ZIP_DEFLATED) as z:
    for p in sorted(Path("artifacts/run-bot").rglob("*")):
        if p.is_file():
            z.write(p, p.as_posix())
PY
