from __future__ import annotations

import subprocess
import sys
from pathlib import Path

PATCHES = [
    Path("scripts/apply_rules_api_budget_policy.py"),
    Path("scripts/apply_odds_budget_boost_policy.py"),
    Path("scripts/apply_odds_api_io_dual_account_patch.py"),
    Path("scripts/apply_publication_same_match_dedupe_patch.py"),
    Path("scripts/check_publication_runtime_syntax.py"),
    Path("scripts/apply_daily_top5_publish_policy.py"),
    Path("scripts/apply_day_inventory_runtime_patch.py"),
    Path("scripts/patch_inventory_tomorrow_and_nearmiss_runtime.py"),
    Path("scripts/patch_near_miss_enrichment_targets.py"),
    Path("scripts/patch_detailed_report_inventory_counts.py"),
    Path("scripts/patch_detailed_report_external_signals.py"),
    Path("scripts/apply_settlement_matching_patch.py"),
]


def run_patch(path: Path) -> None:
    if not path.exists():
        print(f"skip: {path}")
        return
    print(f"running: {path}")
    proc = subprocess.run([sys.executable, str(path)], check=False)
    if path.name == "check_publication_runtime_syntax.py" and proc.returncode != 0:
        raise SystemExit(proc.returncode)


def main() -> int:
    for path in PATCHES:
        run_patch(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
