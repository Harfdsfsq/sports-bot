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
    results: dict[str, object] = {'patches': [], 'modules': [], 'instantiation': {}}
    for patch in PATCHES:
        proc = subprocess.run([sys.executable, patch], cwd=str(ROOT), text=True, capture_output=True, timeout=30)
        results['patches'].append({
            'patch': patch,
            'returncode': proc.returncode,
            'stdout_tail': proc.stdout[-1000:],
            'stderr_tail': proc.stderr[-1000:],
        })
        if proc.returncode != 0:
            raise SystemExit(proc.returncode)

    sys.path.insert(0, str(ROOT))
    for module_name in MODULES:
        importlib.import_module(module_name)
        results['modules'].append({'module': module_name, 'status': 'ok'})

    # Import-only is not enough: the last failure happened during PredictionRunner(settings).
    from app.config import Settings
    from app.services.runner import PredictionRunner

    settings = Settings()
    runner = PredictionRunner(settings)
    results['instantiation'] = {
        'PredictionRunner': 'ok',
        'external_signals_attr': hasattr(runner, 'external_signals'),
        'external_signals_loaded': runner.external_signals is not None,
        'provider_status_external_signals': runner.provider_status.get('external_signals'),
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(results, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    print(json.dumps({'status': 'ok', **results}, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
