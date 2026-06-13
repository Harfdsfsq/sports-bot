from __future__ import annotations

import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path('.').resolve()
OUT = ROOT / '.data' / 'exports' / 'latest-artifact-prune-status.json'

DELETE_DIRS = [
    ROOT / 'artifacts' / 'run-bot' / 'cache',
    ROOT / 'artifacts' / 'run-bot' / 'exports' / '2026-06-13',
]

# Generic date-folder pruning under exports; keep latest-* files.
EXPORT_DIRS = [ROOT / 'artifacts' / 'run-bot' / 'exports', ROOT / '.data' / 'exports']

KEEP_PREFIXES = ('latest-',)
KEEP_SUFFIXES = ('.json', '.txt', '.csv', '.md', '.log')
MAX_FILE_MB = float(os.getenv('HARIZON_ARTIFACT_PRUNE_MAX_FILE_MB', '40'))


def _safe_size(path: Path) -> int:
    try:
        if path.is_file():
            return path.stat().st_size
        return sum(p.stat().st_size for p in path.rglob('*') if p.is_file())
    except Exception:
        return 0


def _delete(path: Path, deleted: list[dict[str, Any]], reason: str) -> None:
    if not path.exists():
        return
    size = _safe_size(path)
    try:
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
        else:
            path.unlink(missing_ok=True)
        deleted.append({'path': str(path), 'size_bytes': size, 'reason': reason})
    except Exception as exc:
        deleted.append({'path': str(path), 'size_bytes': size, 'reason': reason, 'error': f'{type(exc).__name__}: {exc}'})


def main() -> int:
    deleted: list[dict[str, Any]] = []
    started = datetime.now(timezone.utc).isoformat()

    for path in DELETE_DIRS:
        _delete(path, deleted, 'known_heavy_duplicate_dir')

    for base in EXPORT_DIRS:
        if not base.exists():
            continue
        for child in list(base.iterdir()):
            if child.is_dir() and child.name[:4].isdigit():
                _delete(child, deleted, 'dated_export_folder')
        for file in list(base.rglob('*')):
            if not file.is_file():
                continue
            name = file.name
            if name.startswith(KEEP_PREFIXES) and name.endswith(KEEP_SUFFIXES):
                if _safe_size(file) > MAX_FILE_MB * 1024 * 1024:
                    _delete(file, deleted, f'large_latest_file_gt_{MAX_FILE_MB}mb')
                continue
            if _safe_size(file) > MAX_FILE_MB * 1024 * 1024:
                _delete(file, deleted, f'large_non_latest_file_gt_{MAX_FILE_MB}mb')

    payload = {
        'created_at_utc': datetime.now(timezone.utc).isoformat(),
        'started_at_utc': started,
        'deleted_count': len(deleted),
        'deleted_bytes': sum(int(x.get('size_bytes') or 0) for x in deleted),
        'deleted_sample': deleted[:100],
        'max_file_mb': MAX_FILE_MB,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    print(json.dumps(payload, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
