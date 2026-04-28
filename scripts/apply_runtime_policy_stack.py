from __future__ import annotations

import subprocess
import sys
from pathlib import Path

PATCHES = [
    Path("scripts/apply_rules_api_budget_policy.py"),
    Path("scripts/apply_odds_budget_boost_policy.py"),
    Path("scripts/apply_daily_top5_publish_policy.py"),
    Path("scripts/apply_day_inventory_runtime_patch.py"),
    Path("scripts/patch_detailed_report_inventory_counts.py"),
]


def run_patch(path: Path) -> None:
    if not path.exists():
        print(f"skip: {path}")
        return
    print(f"running: {path}")
    subprocess.run([sys.executable, str(path)], check=False)


def main() -> int:
    for path in PATCHES:
        run_patch(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
