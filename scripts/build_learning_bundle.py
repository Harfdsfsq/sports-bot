from __future__ import annotations

import argparse
import json
import os
import shutil
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

UTC = timezone.utc

DEFAULT_PATTERNS = [
    ".logs/debug-last-run.json",
    ".data/state.json",
    ".data/exports/latest-*.json",
    ".data/exports/latest-*.csv",
    ".data/exports/*/quality-*.json",
    ".data/exports/*/quality-*.csv",
    ".data/exports/*/daily-*.json",
    ".data/exports/*/daily-*.csv",
    ".data/exports/market-monitor/*.json",
]

def _iter_matches(root: Path, patterns: Iterable[str]) -> list[Path]:
    found: dict[str, Path] = {}
    for pattern in patterns:
        for path in root.glob(pattern):
            if path.is_file():
                found[str(path.resolve())] = path
    return sorted(found.values())

def _recent_run_files(root: Path, limit: int) -> list[Path]:
    runs_root = root / ".logs" / "runs"
    if not runs_root.exists():
        return []
    files = [p for p in runs_root.glob("*/*-run.json") if p.is_file()]
    files.sort(key=lambda p: (p.parent.name, p.name), reverse=True)
    return files[: max(0, limit)]

def _copy_files(root: Path, dest: Path, files: list[Path]) -> list[dict]:
    manifest_rows = []
    for src in files:
        rel = src.relative_to(root)
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, target)
        manifest_rows.append({
            "path": str(rel).replace("\\", "/"),
            "size_bytes": src.stat().st_size,
            "modified_at": datetime.fromtimestamp(src.stat().st_mtime, tz=UTC).isoformat(),
        })
    return manifest_rows

def _zip_dir(source_dir: Path, zip_path: Path) -> None:
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for path in source_dir.rglob("*"):
            if path.is_file():
                zf.write(path, path.relative_to(source_dir))

def main() -> int:
    parser = argparse.ArgumentParser(description="Create a compact learning/diagnostic bundle from bot state.")
    parser.add_argument("--root", default=".", help="Repository root")
    parser.add_argument("--work-dir", default="artifacts/learning-bundle", help="Temporary directory")
    parser.add_argument("--output", default="artifacts/learning-bundle.zip", help="Output zip path")
    parser.add_argument("--recent-runs", type=int, default=20, help="How many recent run archives to include")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    work_dir = Path(args.work_dir).resolve()
    output = Path(args.output).resolve()

    if work_dir.exists():
        shutil.rmtree(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)

    files = _iter_matches(root, DEFAULT_PATTERNS)
    files.extend(_recent_run_files(root, args.recent_runs))

    unique: dict[str, Path] = {}
    for item in files:
        unique[str(item.resolve())] = item
    selected = sorted(unique.values())

    manifest = {
        "created_at": datetime.now(UTC).isoformat(),
        "root": str(root),
        "file_count": len(selected),
        "files": _copy_files(root, work_dir, selected),
    }
    manifest_path = work_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    summary_md = work_dir / "README.md"
    summary_md.write_text(
        "\n".join([
            "# HARIZON learning bundle",
            "",
            "Use this bundle for post-run analysis.",
            "",
            f"- Created at: {manifest['created_at']}",
            f"- Files included: {manifest['file_count']}",
            f"- Recent run archives: {min(args.recent_runs, len(_recent_run_files(root, args.recent_runs)))}",
            "",
            "Recommended files to inspect first:",
            "- .logs/debug-last-run.json",
            "- .data/state.json",
            "- latest quality report/json/csv",
            "- latest daily report/json/csv",
            "- recent .logs/runs/*/*-run.json",
            "",
        ]),
        encoding="utf-8",
    )

    _zip_dir(work_dir, output)
    print(json.dumps({
        "ok": True,
        "bundle_zip": str(output),
        "work_dir": str(work_dir),
        "file_count": manifest["file_count"],
    }, ensure_ascii=False, indent=2))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
