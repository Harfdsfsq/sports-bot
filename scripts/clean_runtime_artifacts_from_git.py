from __future__ import annotations

"""Remove runtime/private artifacts from the Git index while keeping local files.

Run once from repository root after replacing files:
    python scripts/clean_runtime_artifacts_from_git.py
Then commit the resulting deletions plus the updated .gitignore.
"""

import subprocess
from pathlib import Path

RUNTIME_PATHS = [
    ".env",
    ".data",
    ".logs",
    "artifacts",
    ".codex_tmp",
    ".pytest_cache",
]


def is_tracked(path: str) -> bool:
    result = subprocess.run(
        ["git", "ls-files", "--error-unmatch", path],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    return result.returncode == 0


def main() -> int:
    if not Path(".git").exists():
        print("error: run this script from the repository root")
        return 2

    tracked = [path for path in RUNTIME_PATHS if is_tracked(path) or subprocess.run(["git", "ls-files", path], capture_output=True, text=True).stdout.strip()]
    if not tracked:
        print("ok: no runtime/private artifact paths are tracked")
        return 0

    cmd = ["git", "rm", "-r", "--cached", "--ignore-unmatch", *tracked]
    print("running:", " ".join(cmd))
    subprocess.run(cmd, check=True)
    print("ok: removed runtime/private artifacts from Git index; local files were kept")
    print("next: git status && git add .gitignore && git commit -m 'Stop tracking runtime artifacts'")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
