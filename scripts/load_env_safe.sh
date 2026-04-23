#!/usr/bin/env bash
set -euo pipefail
ENV_FILE="${1:-}"
if [[ -z "$ENV_FILE" || ! -f "$ENV_FILE" ]]; then
  echo "usage: load_env_safe.sh path/to/file.env" >&2
  exit 1
fi
grep -E '^[A-Za-z_][A-Za-z0-9_]*=' "$ENV_FILE" >> "$GITHUB_ENV"
