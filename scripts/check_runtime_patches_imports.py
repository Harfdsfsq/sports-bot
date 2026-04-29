from __future__ import annotations

import importlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path('.').resolve()
OUT = ROOT / '.data' / 'exports' / 'latest-runtime-patch-import-check.json'
PATCHES = [
    'scripts/apply_external_signals_runtime_patch.py',
    'scripts/apply_settlement_matching_patch.py',
]
MODULES = [
    'app.services.runner',
    'app.providers.external_signals',
    'app.services.settlement',
]


def main() -> int:
    results: dict[str, object] = {'patches': [], 'modules': []}
    for patch in PATCHES:
        proc = subprocess.run([sys.executable, patch], cwd=str(ROOT), text=True, capture_output=True, timeout=30)
        results['patches'].append({
            'patch': patch,
            'returncode': proc.returncode,
            'stdout_tail': proc.stdout[-800:],
            'stderr_tail': proc.stderr[-800:],
        })
        if proc.returncode != 0:
            raise SystemExit(proc.returncode)
    sys.path.insert(0, str(ROOT))
    for module_name in MODULES:
        importlib.import_module(module_name)
        results['modules'].append({'module': module_name, 'status': 'ok'})
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(results, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    print(json.dumps({'status': 'ok', **results}, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
