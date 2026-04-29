from __future__ import annotations

import json
import py_compile
import subprocess
import sys
from pathlib import Path

ROOT = Path('.').resolve()
OUT = ROOT / '.data' / 'exports' / 'latest-publication-runtime-syntax-check.json'
PATCHES = [
    'scripts/apply_publication_same_match_dedupe_patch.py',
]
FILES = [
    'scripts/publish_controlled_fallback.py',
]


def main() -> int:
    report: dict[str, object] = {'status': 'ok', 'patches': [], 'compiled': []}
    for patch in PATCHES:
        proc = subprocess.run([sys.executable, patch], cwd=str(ROOT), text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, timeout=30)
        report['patches'].append({'patch': patch, 'returncode': proc.returncode, 'stdout_tail': proc.stdout[-1000:], 'stderr_tail': proc.stderr[-1000:]})
        if proc.returncode != 0:
            report['status'] = 'patch_failed'
            OUT.parent.mkdir(parents=True, exist_ok=True)
            OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')
            print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
            return proc.returncode
    for rel in FILES:
        py_compile.compile(str(ROOT / rel), doraise=True)
        report['compiled'].append({'file': rel, 'status': 'ok'})
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
