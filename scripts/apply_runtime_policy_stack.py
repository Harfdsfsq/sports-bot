from __future__ import annotations

import subprocess
import sys
from pathlib import Path

# Clean runtime stack. Legacy one-off patches are intentionally not executed here.
# Important fixes now live either in normal code or in apply_clean_runtime_system.py.
PATCHES = [
    Path("scripts/apply_clean_runtime_system.py"),
    Path("scripts/patch_provider_budget_clean_runtime.py"),
    Path("scripts/apply_publication_same_match_dedupe_patch.py"),
    Path("scripts/check_publication_runtime_syntax.py"),
    Path("scripts/patch_detailed_report_inventory_counts.py"),
    Path("scripts/apply_settlement_matching_patch.py"),
]

REQUIRED = {
    "apply_clean_runtime_system.py",
    "patch_provider_budget_clean_runtime.py",
    "check_publication_runtime_syntax.py",
}


def run_patch(path: Path) -> None:
    if not path.exists():
        print(f"skip: {path}")
        if path.name in REQUIRED:
            raise SystemExit(f"required runtime script missing: {path}")
        return
    print(f"running: {path}")
    proc = subprocess.run([sys.executable, str(path)], check=False)
    if path.name in REQUIRED and proc.returncode != 0:
        raise SystemExit(proc.returncode)


def main() -> int:
    for path in PATCHES:
        run_patch(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
