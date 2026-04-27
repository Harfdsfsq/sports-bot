from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Iterable

ROOT = Path(".").resolve()

STATE_FILES = [
    ".data/state.json",
    ".data/fallback-sent-index.json",
    ".data/provider_quota_governor_state.json",
    ".data/provider_quota_state.json",
    ".data/daily-ops-report-sent.json",
    ".data/detailed-run-report-sent.json",
    ".data/calibration-profile.json",
    ".data/auto_learning_runtime_overrides.env",
    ".data/learning-state.json",
    ".data/autorun-state.json",
    ".data/volume-governor-state.json",
    ".data/provider_request_budget_state.json",
    ".data/exports/latest-run-summary.json",
]


def run(cmd: list[str], *, check: bool = False) -> subprocess.CompletedProcess[str]:
    print("+", " ".join(cmd), flush=True)
    return subprocess.run(
        cmd,
        cwd=ROOT,
        text=True,
        stdout=sys.stdout,
        stderr=sys.stderr,
        check=check,
    )


def is_git_repo() -> bool:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        return result.stdout.strip() == "true"
    except Exception:
        return False


def copy_existing_state(dst: Path) -> list[str]:
    copied: list[str] = []
    for rel in STATE_FILES:
        src = ROOT / rel
        if not src.exists() or not src.is_file():
            continue
        target = dst / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, target)
        copied.append(rel)
    return copied


def restore_state(src_root: Path, files: Iterable[str]) -> None:
    for rel in files:
        src = src_root / rel
        if not src.exists():
            continue
        dst = ROOT / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)


def has_staged_changes() -> bool:
    result = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=ROOT)
    return result.returncode != 0


def stage_state(files: Iterable[str]) -> None:
    existing = [rel for rel in files if (ROOT / rel).exists()]
    if not existing:
        print("No persistent state files exist; nothing to sync.")
        return
    run(["git", "add", "-f", *existing], check=False)


def configure_git() -> None:
    run(["git", "config", "user.name", os.getenv("GIT_AUTHOR_NAME", "github-actions")], check=False)
    run(["git", "config", "user.email", os.getenv("GIT_AUTHOR_EMAIL", "github-actions@users.noreply.github.com")], check=False)


def reset_to_origin() -> bool:
    fetch = run(["git", "fetch", "origin", "main"], check=False)
    if fetch.returncode != 0:
        print("git fetch failed; will try to commit/push from current checkout without failing the job.")
        return False
    reset = run(["git", "reset", "--hard", "origin/main"], check=False)
    if reset.returncode != 0:
        print("git reset --hard origin/main failed; continuing best-effort.")
        return False
    return True


def commit_state(message: str) -> bool:
    if not has_staged_changes():
        print("No staged persistent state changes.")
        return False
    commit = run(["git", "commit", "-m", message], check=False)
    return commit.returncode == 0


def push_state() -> bool:
    push = run(["git", "push", "origin", "HEAD:main"], check=False)
    return push.returncode == 0


def sync_once(tmp: Path, copied: list[str], message: str, *, reset_first: bool) -> bool:
    if reset_first:
        reset_to_origin()
    restore_state(tmp, copied)
    stage_state(copied)
    committed = commit_state(message)
    if not committed:
        return True
    return push_state()


def main() -> int:
    if not is_git_repo():
        print("Not a git repository; skipping persistent state sync.")
        return 0

    configure_git()

    with tempfile.TemporaryDirectory(prefix="bot-state-sync-") as raw_tmp:
        tmp = Path(raw_tmp)
        copied = copy_existing_state(tmp)
        if not copied:
            print("No state files copied; skipping.")
            return 0

        print(json.dumps({"persistent_state_files": copied}, ensure_ascii=False, indent=2))

        message = os.getenv("PERSISTENT_STATE_COMMIT_MESSAGE") or "Update persistent bot state"

        ok = sync_once(tmp, copied, message, reset_first=True)
        if ok:
            print("Persistent state sync completed or nothing changed.")
            return 0

        print("First push failed. Retrying once from latest origin/main...")
        ok = sync_once(tmp, copied, message, reset_first=True)
        if ok:
            print("Persistent state sync completed on retry.")
            return 0

        print("WARNING: persistent state push failed after retry; leaving workflow successful.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
