from __future__ import annotations

import json
from pathlib import Path
import shutil
import zipfile

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXED_DIR = REPO_ROOT / "artifacts/fixed-run"


def copy_if_exists(src: Path, dst: Path) -> None:
    if not src.exists():
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    if src.is_dir():
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(src, dst)
    else:
        shutil.copy2(src, dst)


def main() -> int:
    if FIXED_DIR.exists():
        shutil.rmtree(FIXED_DIR)
    FIXED_DIR.mkdir(parents=True, exist_ok=True)

    mapping = {
        REPO_ROOT / ".logs/debug-last-run.json": FIXED_DIR / "debug-last-run.json",
        REPO_ROOT / ".data/state.json": FIXED_DIR / "state.json",
        REPO_ROOT / ".data/exports/latest-picks.json": FIXED_DIR / "latest-picks.json",
        REPO_ROOT / ".data/exports/latest-bets.json": FIXED_DIR / "latest-bets.json",
        REPO_ROOT / ".data/exports/latest-quality-report.json": FIXED_DIR / "latest-quality-report.json",
        REPO_ROOT / "artifacts/fixed-run/latest-canonical-picks.json": FIXED_DIR / "latest-canonical-picks.json",
        REPO_ROOT / "artifacts/fixed-run/latest-candidate-integrity.json": FIXED_DIR / "latest-candidate-integrity.json",
        REPO_ROOT / "artifacts/fixed-run/latest-odds-integrity-report.json": FIXED_DIR / "latest-odds-integrity-report.json",
    }
    for src, dst in mapping.items():
        if src.resolve() == dst.resolve():
            continue
        copy_if_exists(src, dst)

    latest_run = None
    runs_root = REPO_ROOT / ".logs/runs"
    if runs_root.exists():
        all_runs = sorted(runs_root.glob("*/*-run.json"))
        if all_runs:
            latest_run = all_runs[-1]
            copy_if_exists(latest_run, FIXED_DIR / "latest-run.json")

    summary = {
        "latest_run_path": str(latest_run) if latest_run else "",
        "bundle_root": str(FIXED_DIR),
    }
    (FIXED_DIR / "bundle-summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    bundle_path = REPO_ROOT / "artifacts/fixed-run-bundle.zip"
    if bundle_path.exists():
        bundle_path.unlink()
    with zipfile.ZipFile(bundle_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(FIXED_DIR.rglob("*")):
            if path.is_file():
                zf.write(path, arcname=str(path.relative_to(FIXED_DIR.parent)))
    print(json.dumps({"bundle_path": str(bundle_path)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
