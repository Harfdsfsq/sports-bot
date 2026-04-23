from __future__ import annotations

import argparse
import json
import os
import zipfile
from pathlib import Path
from typing import Iterable


def _bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _iter_paths(base: Path, patterns: Iterable[str]) -> list[Path]:
    paths: list[Path] = []
    for pattern in patterns:
        paths.extend(base.glob(pattern))
    return sorted({path for path in paths if path.exists()})


def _write_zip(zip_path: Path, items: list[Path]) -> None:
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as handle:
        for path in items:
            if path.is_dir():
                for child in sorted(path.rglob("*")):
                    if child.is_file():
                        handle.write(child, child.as_posix())
            elif path.is_file():
                handle.write(path, path.as_posix())


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a compact learning bundle from local bot artifacts.")
    parser.add_argument("--output", default="artifacts/learning-bundle.zip")
    parser.add_argument("--runs-root", default=".logs/runs")
    parser.add_argument("--exports-root", default=".data/exports")
    parser.add_argument("--state", default=".data/state.json")
    parser.add_argument("--debug", default=".logs/debug-last-run.json")
    parser.add_argument("--report", default="artifacts/latest-run-summary.json")
    args = parser.parse_args()

    items: list[Path] = []
    runs_root = Path(args.runs_root)
    exports_root = Path(args.exports_root)

    if _bool_env("LEARNING_BUNDLE_INCLUDE_DEBUG", True):
        items.extend(_iter_paths(Path("."), [args.debug]))
    if _bool_env("LEARNING_BUNDLE_INCLUDE_STATE", True):
        items.extend(_iter_paths(Path("."), [args.state]))
    if runs_root.exists():
        max_runs = int(os.getenv("LEARNING_BUNDLE_MAX_RUNS", "30"))
        run_files = sorted(runs_root.glob("*/*-run.json"))[-max_runs:]
        items.extend(run_files)
    if _bool_env("LEARNING_BUNDLE_INCLUDE_EXPORTS", True) and exports_root.exists():
        items.extend(_iter_paths(exports_root, [
            "latest-*.json",
            "latest-*.csv",
            "*/quality-*.json",
            "*/quality-*.csv",
            "*/daily-*.json",
            "*/daily-*.csv",
        ]))
    items.extend(_iter_paths(Path("."), [args.report]))
    items.extend(_iter_paths(Path("artifacts"), [
        "odds-integrity-report.json",
        "odds-traces/*.json",
    ]))

    # de-dupe, preserve order
    seen: set[str] = set()
    deduped: list[Path] = []
    for item in items:
        key = str(item.resolve())
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)

    _write_zip(Path(args.output), deduped)
    payload = {"files_added": len(deduped), "output": str(args.output)}
    Path(args.report).parent.mkdir(parents=True, exist_ok=True)
    Path(args.report).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
