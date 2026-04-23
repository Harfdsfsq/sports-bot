from __future__ import annotations

import json
import zipfile
from pathlib import Path

ROOT = Path(".")
ARTIFACTS = ROOT / "artifacts"

INCLUDE_PATHS = [
    ROOT / ".logs" / "debug-last-run.json",
    ROOT / ".logs" / "runs",
    ROOT / ".data" / "state.json",
    ROOT / ".data" / "exports",
    ARTIFACTS / "latest-candidate-integrity.json",
    ARTIFACTS / "latest-canonical-picks.json",
    ARTIFACTS / "canonical-publish-report.json",
]

def main() -> int:
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    target = ARTIFACTS / "internal-pipeline-bundle.zip"
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in INCLUDE_PATHS:
            if not path.exists():
                continue
            if path.is_file():
                zf.write(path, path.relative_to(ROOT))
            else:
                for child in path.rglob("*"):
                    if child.is_file():
                        zf.write(child, child.relative_to(ROOT))
    summary = {
        "bundle_path": str(target),
        "exists": target.exists(),
        "size": target.stat().st_size if target.exists() else 0,
    }
    (ARTIFACTS / "internal-pipeline-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
