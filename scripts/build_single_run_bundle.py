from __future__ import annotations

import json
import zipfile
from pathlib import Path

INCLUDE = [
    Path(".logs/debug-last-run.json"),
    Path(".logs/runs"),
    Path(".data/state.json"),
    Path(".data/exports"),
]

def main() -> int:
    out_dir = Path("artifacts")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_zip = out_dir / "internal-pipeline-bundle.zip"
    count = 0
    with zipfile.ZipFile(out_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for root in INCLUDE:
            if not root.exists():
                continue
            if root.is_file():
                zf.write(root, root.as_posix())
                count += 1
                continue
            for path in root.rglob("*"):
                if path.is_file():
                    zf.write(path, path.as_posix())
                    count += 1
    summary = {"bundle_path": str(out_zip), "files": count}
    (out_dir / "internal-pipeline-summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
