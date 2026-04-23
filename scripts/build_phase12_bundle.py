from __future__ import annotations

import json
import shutil
from pathlib import Path
import zipfile

ROOT = Path('.')
ART = ROOT / 'artifacts'
TARGET = ART / 'phase12-bundle'
TARGET.mkdir(parents=True, exist_ok=True)

CANDIDATES = [
    Path('.logs/debug-last-run.json'),
    Path('.data/state.json'),
    Path('.data/exports/latest-picks.json'),
    Path('.data/exports/latest-bets.json'),
    Path('.data/exports/latest-quality-report.json'),
    Path('.data/exports/latest-daily-report.json'),
    Path('artifacts/latest-candidate-integrity.json'),
    Path('artifacts/latest-canonical-picks.json'),
    Path('artifacts/publish-gate-report.json'),
]

for src in CANDIDATES:
    if src.exists() and src.is_file():
        dst = TARGET / src.name
        dst.write_bytes(src.read_bytes())

bundle_path = ART / 'phase12-bundle.zip'
with zipfile.ZipFile(bundle_path, 'w', zipfile.ZIP_DEFLATED) as zf:
    for path in sorted(TARGET.glob('*')):
        zf.write(path, arcname=f'phase12-bundle/{path.name}')

summary = {
    'bundle_path': str(bundle_path),
    'files': sorted([p.name for p in TARGET.glob('*')]),
}
(ART / 'phase12-bundle-summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(summary, ensure_ascii=False))
