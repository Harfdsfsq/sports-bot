"""Run the native regression suite in an isolated, credential-free repository."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

TESTS = (
    "daily_quality_pipeline",
    "api_budget",
    "daily_context_reliability",
    "daily_market_monitor",
    "goal_research",
    "totals_strategy_lab",
    "inventory_identity_integrity",
    "branch_state_migration",
    "coverage_result_repairs",
    "free_api_enrichment",
    "native_data_expansion",
    "prospective_validation",
    "release_and_quotes",
    "ci_repairs",
)
EXTRA_TESTS = (
    "tests/test_providers_and_telegram.py::test_odds_api_io_stops_event_paging_on_auth_error",
    "tests/test_providers_and_telegram.py::test_odds_api_io_full_auth_failure_activates_cooldown",
)


def main():
    root = Path(__file__).resolve().parents[1]
    env = dict(os.environ)
    for line in (root / "config/daily_quality.env").read_text().splitlines():
        if line and not line.startswith("#"):
            key, value = line.split("=", 1)
            env[key] = value
    env = {
        key: value
        for key, value in env.items()
        if not any(token in key for token in ("_KEY", "TOKEN", "SECRET", "PASSWORD", "CHAT_ID"))
    }
    with tempfile.TemporaryDirectory() as directory:
        for folder in ("app", "scripts", "config", "tests", "docs", ".github"):
            shutil.copytree(
                root / folder, Path(directory) / folder, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache")
            )
        env["PYTHONPATH"] = directory
        return subprocess.call(
            [sys.executable, "-m", "pytest", "-q", *("tests/test_" + name + ".py" for name in TESTS), *EXTRA_TESTS],
            cwd=directory,
            env=env,
        )


if __name__ == "__main__":
    raise SystemExit(main())
