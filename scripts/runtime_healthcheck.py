from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

REQUIRED_FILES = [
    ".github/workflows/run-bot.yml",
    ".github/workflows/daily-report.yml",
    "scripts/publish_controlled_fallback.py",
    "scripts/build_detailed_run_report.py",
    "scripts/build_daily_ops_report.py",
    "scripts/auto_learning_engine.py",
    "scripts/sync_persistent_state.py",
    "scripts/apply_provider_request_budget.py",
    "config/final_runtime_overrides.env",
    "config/provider_request_budget.json",
    "config/auto_learning_policy.json",
    "app/services/telegram_i18n.py",
]

GENERATED_DIRS = [
    ".data/exports",
    ".data/provider_cache",
    ".logs",
    "artifacts",
]

STATE_FILES = [
    ".data/state.json",
    ".data/fallback-sent-index.json",
    ".data/provider_quota_governor_state.json",
    ".data/provider_quota_state.json",
    ".data/calibration-profile.json",
    ".data/learning-state.json",
    ".data/auto_learning_runtime_overrides.env",
]


def read(path: str) -> str:
    try:
        return Path(path).read_text(encoding="utf-8")
    except Exception:
        return ""


def main() -> int:
    missing = [path for path in REQUIRED_FILES if not Path(path).exists()]
    warnings = []

    run_yml = read(".github/workflows/run-bot.yml")
    if "git add -A -f .data .logs artifacts" in run_yml:
        warnings.append("run-bot.yml still contains unsafe git add -A -f generated-output pattern")
    if "python scripts/auto_learning_engine.py" not in run_yml:
        warnings.append("run-bot.yml does not call auto_learning_engine.py")
    if "python scripts/sync_persistent_state.py || true" not in run_yml:
        warnings.append("run-bot.yml does not use safe persistent state sync")
    if "config/api_quota_governor.env" in run_yml or "apply_provider_quota_governor.py" in run_yml:
        warnings.append("run-bot.yml still applies the legacy quota governor; provider_request_budget must be the only quota source")
    if "python scripts/apply_provider_request_budget.py" not in run_yml:
        warnings.append("run-bot.yml does not apply provider_request_budget.py")
    if "push:" in run_yml and ("main" in run_yml or "master" in run_yml):
        warnings.append("run-bot.yml still has a push trigger; merge commits should not auto-run the bot")
    if "workflow_dispatch" not in run_yml or "DETAILED_RUN_REPORT_SEND_TELEGRAM" not in run_yml:
        warnings.append("detailed run report Telegram policy should be enabled for workflow_dispatch")

    gitignore = read(".gitignore")
    for state in STATE_FILES:
        marker = "!" + state
        if marker not in gitignore:
            warnings.append(f".gitignore does not explicitly keep {state}")

    env_text = read(".env")
    dangerous_defaults = [
        "ODDSPAPI_ENABLED=true",
        "ALLSPORTSAPI_ENABLED=true",
        "API_FOOTBALL_ENABLED=true",
        "FUTRIXMETRICS_ENABLED=true",
        "ENABLE_NEWSAPI_CONTEXT=true",
        "ENABLE_GNEWS_CONTEXT=true",
        "ENABLE_FOOTBALL_DATA_CONTEXT=true",
        "ENABLE_THESPORTSDB_CONTEXT=true",
    ]
    for marker in dangerous_defaults:
        if marker in env_text:
            warnings.append(f".env still has dangerous budget-bypassing default: {marker}")

    generated_present = [path for path in GENERATED_DIRS if Path(path).exists()]
    payload: dict[str, Any] = {
        "ok": not missing,
        "missing_required_files": missing,
        "warnings": warnings,
        "generated_dirs_present": generated_present,
        "state_files_present": [path for path in STATE_FILES if Path(path).exists()],
    }

    Path(".data/exports").mkdir(parents=True, exist_ok=True)
    Path(".data/exports/latest-runtime-healthcheck.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(json.dumps(payload, ensure_ascii=False, indent=2))
    # Do not fail the workflow for warnings; fail only if critical files are missing.
    return 1 if missing else 0


if __name__ == "__main__":
    raise SystemExit(main())
