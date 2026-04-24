from __future__ import annotations

import json
import zipfile
from pathlib import Path
from typing import Iterable


BUNDLE_DIR = Path("artifacts/run-bot")
BUNDLE_ZIP = Path("artifacts/run-bot-bundle.zip")


def copy_if_exists(src: Path, dst: Path) -> None:
    if not src.exists() or not src.is_file():
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(src.read_bytes())


def iter_files(root: Path) -> Iterable[Path]:
    if not root.exists():
        return []
    return [path for path in root.rglob("*") if path.is_file()]


def main() -> int:
    BUNDLE_DIR.mkdir(parents=True, exist_ok=True)

    files = {
        ".logs/debug-last-run.json": BUNDLE_DIR / "debug-last-run.json",
        ".data/state.json": BUNDLE_DIR / "state.json",
        ".data/exports/latest-picks.json": BUNDLE_DIR / "latest-picks.json",
        ".data/exports/latest-bets.json": BUNDLE_DIR / "latest-bets.json",
        ".data/exports/latest-matches.json": BUNDLE_DIR / "latest-matches.json",
        ".data/exports/latest-quality-report.json": BUNDLE_DIR / "latest-quality-report.json",
        ".data/exports/latest-candidate-integrity.json": BUNDLE_DIR / "latest-candidate-integrity.json",
        ".data/exports/latest-coverage-audit.json": BUNDLE_DIR / "latest-coverage-audit.json",
        ".data/exports/latest-history-guard-audit.json": BUNDLE_DIR / "latest-history-guard-audit.json",
        "artifacts/run-bot/latest-run-summary.json": BUNDLE_DIR / "latest-run-summary.json",
    }
    for src, dst in files.items():
        copy_if_exists(Path(src), dst)

    # Include the newest run archive if present.
    run_files = sorted(Path(".logs/runs").glob("*/*-run.json")) if Path(".logs/runs").exists() else []
    if run_files:
        copy_if_exists(run_files[-1], BUNDLE_DIR / "latest-run.json")

    summary = {
        "bundle_dir": str(BUNDLE_DIR),
        "latest_run_archive": str(run_files[-1]) if run_files else "",
        "files": sorted(str(path.relative_to(BUNDLE_DIR)) for path in iter_files(BUNDLE_DIR)),
    }
    (BUNDLE_DIR / "bundle-summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    BUNDLE_ZIP.parent.mkdir(parents=True, exist_ok=True)
    if BUNDLE_ZIP.exists():
        BUNDLE_ZIP.unlink()
    with zipfile.ZipFile(BUNDLE_ZIP, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(iter_files(BUNDLE_DIR)):
            archive.write(path, path.relative_to("artifacts"))
    print(json.dumps({"bundle_path": str(BUNDLE_ZIP), "files": len(summary["files"])}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
