from __future__ import annotations

import json
import shutil
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

UTC = timezone.utc


def _copy(src: Path, dst: Path) -> bool:
    if not src.exists():
        return False
    dst.parent.mkdir(parents=True, exist_ok=True)
    if src.is_dir():
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(src, dst)
    else:
        shutil.copy2(src, dst)
    return True


def _latest_run_archive() -> Path | None:
    root = Path(".logs/runs")
    if not root.exists():
        return None
    files = [p for p in root.glob("*/*-run.json") if p.is_file()]
    return sorted(files, key=lambda p: (p.parent.name, p.name))[-1] if files else None


def _zip_dir(root: Path, zip_path: Path) -> None:
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for path in sorted(root.rglob("*")):
            if path.is_file() and path != zip_path:
                z.write(path, path.relative_to(root.parent))


def main() -> int:
    bundle = Path("artifacts/run-bot")
    if bundle.exists():
        shutil.rmtree(bundle)
    bundle.mkdir(parents=True, exist_ok=True)

    copied: list[str] = []
    files = [
        Path(".logs/debug-last-run.json"),
        Path(".data/state.json"),
        Path(".data/exports/latest-picks.json"),
        Path(".data/exports/latest-bets.json"),
        Path(".data/exports/latest-matches.json"),
        Path(".data/exports/latest-quality-report.json"),
        Path(".data/exports/latest-quality-segments.csv"),
        Path(".data/exports/latest-quality-learning.csv"),
        Path(".data/exports/latest-candidate-integrity.json"),
        Path(".data/exports/latest-candidate-integrity.csv"),
        Path(".data/exports/latest-run-summary.json"),
        Path(".data/exports/latest-run-summary.md"),
        Path(".data/exports/latest-coverage-audit.json"),
        Path(".data/exports/latest-history-guard-audit.json"),
        Path(".data/exports/latest-training-dataset.json"),
        Path(".data/exports/latest-reporting-sqlite.json"),
    ]
    latest = _latest_run_archive()
    if latest is not None:
        files.append(latest)

    for src in files:
        if not src.exists():
            continue
        name = "latest-run.json" if latest is not None and src == latest else src.name
        if _copy(src, bundle / name):
            copied.append(str(src))

    summary = {
        "created_at": datetime.now(UTC).isoformat(),
        "bundle_dir": str(bundle),
        "latest_run_archive": str(latest) if latest else None,
        "files_copied": copied,
    }
    (bundle / "bundle-summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    zip_path = Path("artifacts/run-bot-bundle.zip")
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    _zip_dir(bundle, zip_path)
    print(json.dumps({"zip_path": str(zip_path), **summary}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
