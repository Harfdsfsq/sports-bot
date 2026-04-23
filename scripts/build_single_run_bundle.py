from __future__ import annotations

import argparse
import json
import zipfile
from pathlib import Path
from typing import Iterable


def _iter_paths(base: Path, patterns: Iterable[str]) -> list[Path]:
    paths: list[Path] = []
    for pattern in patterns:
        paths.extend(base.glob(pattern))
    return sorted({path for path in paths if path.exists()})


def _latest_run_path(runs_root: Path) -> Path | None:
    paths = sorted(runs_root.glob("*/*-run.json")) if runs_root.exists() else []
    return paths[-1] if paths else None


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
    parser = argparse.ArgumentParser(description="Build a compact single-run bundle from local bot artifacts.")
    parser.add_argument("--output", default="artifacts/single-run-bundle.zip")
    parser.add_argument("--runs-root", default=".logs/runs")
    parser.add_argument("--exports-root", default=".data/exports")
    parser.add_argument("--state", default=".data/state.json")
    parser.add_argument("--debug", default=".logs/debug-last-run.json")
    parser.add_argument("--report", default="artifacts/latest-run-summary.json")
    args = parser.parse_args()

    items: list[Path] = []
    latest_run = _latest_run_path(Path(args.runs_root))
    if latest_run is not None:
        items.append(latest_run)
    items.extend(_iter_paths(Path("."), [args.state, args.debug, args.report]))
    items.extend(_iter_paths(Path("artifacts"), ["odds-integrity-report.json", "odds-traces/*.json"]))
    items.extend(_iter_paths(Path(args.exports_root), ["latest-*.json", "latest-*.csv"]))

    seen: set[str] = set()
    deduped: list[Path] = []
    for item in items:
        key = str(item.resolve())
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)

    _write_zip(Path(args.output), deduped)
    payload = {"files_added": len(deduped), "output": str(args.output), "latest_run": str(latest_run) if latest_run else ""}
    Path("artifacts/single-run-bundle-summary.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
