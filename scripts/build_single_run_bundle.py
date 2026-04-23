#!/usr/bin/env python3
from __future__ import annotations
import json
import zipfile
from pathlib import Path

ART = Path("artifacts")
ART.mkdir(parents=True, exist_ok=True)

FILES = [
    Path(".data/exports/main-clean-publish-report.json"),
    Path(".data/exports/odds-integrity-report.json"),
    Path(".data/exports/latest-picks.json"),
    Path(".data/exports/latest-picks.csv"),
    Path(".data/exports/latest-run-summary.json"),
    Path(".logs/debug-last-run.json"),
    Path(".data/state.json"),
]

RUNS_ROOT = Path(".logs/runs")
ZIP_PATH = ART / "main-clean-single-run-bundle.zip"

with zipfile.ZipFile(ZIP_PATH, "w", compression=zipfile.ZIP_DEFLATED) as zf:
    for path in FILES:
        if path.exists():
            zf.write(path, path.as_posix())
    if RUNS_ROOT.exists():
        for path in RUNS_ROOT.rglob("*"):
            if path.is_file():
                zf.write(path, path.as_posix())

print(json.dumps({"bundle_path": str(ZIP_PATH)}, ensure_ascii=False, indent=2))
