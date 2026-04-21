#!/usr/bin/env bash
set -Eeuo pipefail

mkdir -p .data .logs .logs/runs

log_path=".logs/run-bot.log"
debug_path=".logs/debug-last-run.json"
legacy_debug_path=".data/debug-last-run.json"

echo "[ci] python version: $(python --version 2>&1)"
echo "[ci] starting bot run"

python -u -m app.cli run-once 2>&1 | tee "${log_path}"
status=${PIPESTATUS[0]}

echo "[ci] bot exit status: ${status}"
if [ -f "${debug_path}" ]; then
  echo "[ci] debug file created"
  wc -c "${debug_path}" || true
elif [ -f "${legacy_debug_path}" ]; then
  echo "[ci] legacy debug file created"
  wc -c "${legacy_debug_path}" || true
else
  echo "[ci] debug file not created"
fi

exit ${status}
