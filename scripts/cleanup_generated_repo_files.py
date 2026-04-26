from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Iterable

ROOT = Path(".").resolve()

KEEP_STATE = {
    ".data/state.json",
    ".data/fallback-sent-index.json",
    ".data/provider_quota_governor_state.json",
    ".data/provider_quota_state.json",
    ".data/daily-ops-report-sent.json",
    ".data/calibration-profile.json",
}

GENERATED_DIRS = [
    ".logs",
    "artifacts",
    ".data/exports",
    ".data/provider_cache",
    ".data/history",
    ".data/tmp",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
]

GENERATED_FILE_PATTERNS = [
    ".data/*.sqlite",
    ".data/*.db",
    ".data/latest-*.json",
    ".data/latest-*.csv",
    ".data/*-report.json",
    ".data/*-summary.json",
    ".data/*-dataset.json",
    ".data/*-audit.json",
    "latest-*.json",
    "latest-*.csv",
    "*.zip",
    "*.tar",
    "*.tar.gz",
    "*.tgz",
    "repo_cleanup_report.json",
    "**/__pycache__",
    "**/*.pyc",
    "**/*.pyo",
]

SUSPECT_PATCHERS = [
    "scripts/apply_global_telegram_i18n_patch.py",
]


def rel(path: Path) -> str:
    return path.resolve().relative_to(ROOT).as_posix()


def inside_git_dir(path: Path) -> bool:
    try:
        return ".git" in path.resolve().relative_to(ROOT).parts
    except Exception:
        return False


def candidate_paths() -> list[Path]:
    out: list[Path] = []

    for item in GENERATED_DIRS:
        path = ROOT / item
        if path.exists():
            out.append(path)

    for pattern in GENERATED_FILE_PATTERNS:
        for path in ROOT.glob(pattern):
            if not path.exists() or inside_git_dir(path):
                continue
            if path.is_file() and rel(path) in KEEP_STATE:
                continue
            out.append(path)

    # Runtime patchers that are now no-op/legacy can be reviewed manually.
    # Do not delete them automatically unless --include-legacy-patchers is set.
    return sorted(set(out), key=lambda p: rel(p))


def run_git_rm(paths: Iterable[Path], *, dry_run: bool) -> list[str]:
    existing = [p for p in paths if p.exists()]
    if not existing:
        return []
    rels = [rel(p) for p in existing]
    cmd = ["git", "rm", "-r", "--ignore-unmatch", "--cached", *rels]
    if dry_run:
        return [" ".join(cmd)]
    try:
        subprocess.run(cmd, cwd=ROOT, check=False)
        return [" ".join(cmd)]
    except Exception as exc:
        return [f"git rm failed: {exc}"]


def delete_worktree(paths: Iterable[Path], *, dry_run: bool) -> list[str]:
    actions = []
    for path in paths:
        if not path.exists() or inside_git_dir(path):
            continue
        r = rel(path)
        if r in KEEP_STATE:
            continue
        actions.append(r)
        if dry_run:
            continue
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
        else:
            try:
                path.unlink()
            except FileNotFoundError:
                pass
    return actions


def main() -> int:
    parser = argparse.ArgumentParser(description="Clean generated repository files safely.")
    parser.add_argument("--apply", action="store_true", help="Actually remove generated files from working tree and git index.")
    parser.add_argument("--include-legacy-patchers", action="store_true", help="Also remove known legacy/no-op patcher scripts.")
    args = parser.parse_args()

    dry_run = not args.apply
    candidates = candidate_paths()

    if args.include_legacy_patchers:
        for item in SUSPECT_PATCHERS:
            path = ROOT / item
            if path.exists():
                candidates.append(path)
        candidates = sorted(set(candidates), key=lambda p: rel(p))

    git_commands = run_git_rm(candidates, dry_run=dry_run)
    removed = delete_worktree(candidates, dry_run=dry_run)

    report = {
        "dry_run": dry_run,
        "candidate_count": len(candidates),
        "candidates": [rel(p) for p in candidates],
        "git_commands": git_commands,
        "removed_or_would_remove": removed,
        "kept_state_files": sorted(KEEP_STATE),
        "note": "Default is dry-run. Re-run with --apply after reviewing candidates in GitHub Desktop.",
    }

    out = ROOT / "repo_cleanup_report.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(report, ensure_ascii=False, indent=2))
    print("\nReport written to repo_cleanup_report.json")
    if dry_run:
        print("Dry-run only. To apply: python scripts/cleanup_generated_repo_files.py --apply")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
