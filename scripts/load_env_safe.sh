#!/usr/bin/env bash
set -euo pipefail

ENV_FILE="${1:-config/main_clean_publish.env}"
if [[ ! -f "$ENV_FILE" ]]; then
  echo "env file not found: $ENV_FILE" >&2
  exit 1
fi

grep -E '^[A-Za-z_][A-Za-z0-9_]*=' "$ENV_FILE" >> "$GITHUB_ENV"
