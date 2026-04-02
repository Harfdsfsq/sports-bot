#!/usr/bin/env bash
set -Eeuo pipefail

mkdir -p .data

echo "[ci] python version: $(python --version 2>&1)"
echo "[ci] starting bot run"

python -u -m app.cli run-once 2>&1 | tee .data/run-bot.log
status=${PIPESTATUS[0]}

echo "[ci] bot exit status: ${status}"
if [ -f .data/debug-last-run.json ]; then
  echo "[ci] debug file created"
  wc -c .data/debug-last-run.json || true
else
  echo "[ci] debug file not created"
fi

exit ${status}
