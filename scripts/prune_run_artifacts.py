from __future__ import annotations

"""Compact HARIZON runtime artifacts before GitHub upload.

The workflow already commits/saves the persistent inventory state.  The upload
artifact should be a review bundle, not a full duplicate of .data/cache and all
nested exports.  This script removes duplicated/heavy folders from artifacts/run-bot
and writes a size report so the step is auditable.
"""

import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path('.').resolve()
EXPORT = ROOT / '.data' / 'exports'
ART = ROOT / 'artifacts' / 'run-bot'
STATUS = EXPORT / 'latest-artifact-prune-status.json'

KEEP_NAMES = {
    'latest-run-bot.log',
    'latest-controlled-fallback-report.json',
    'latest-controlled-fallback-prepublish-guard.json',
    'latest-harizon-telegram-run-report.txt',
    'latest-harizon-telegram-run-report.json',
    'latest-harizon-telegram-run-report-v9.json',
    'latest-harizon-telegram-run-report-v9.txt',
    'latest-harizon-telegram-run-report-v10-status.json',
    'latest-day-inventory-target-expand.json',
    'latest-day-inventory-coverage-truth.json',
    'latest-day-inventory-coverage-truth.csv',
    'latest-day-inventory-cumulative-coverage.json',
    'latest-inventory-bookmaker-backfill.json',
    'latest-b-cover-candidate-gap-report.json',
    'latest-b-cover-candidate-gap-report.csv',
    'latest-b-cover-value-promotion.json',
    'latest-provider-smoke.json',
    'latest-provider-smoke.md',
    'latest-publication-status.json',
    'latest-normalized-publication-payloads.json',
}


def _size(path: Path) -> int:
    if not path.exists():
        return 0
    if path.is_file():
        try:
            return path.stat().st_size
        except OSError:
            return 0
    total = 0
    for f in path.rglob('*'):
        if f.is_file():
            try:
                total += f.stat().st_size
            except OSError:
                pass
    return total


def _remove(path: Path, removed: list[dict[str, object]]) -> None:
    if not path.exists():
        return
    removed.append({'path': str(path), 'bytes': _size(path)})
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=True)
    else:
        try:
            path.unlink()
        except OSError:
            pass


def _copy_file(src: Path, dst: Path) -> None:
    if not src.exists() or not src.is_file():
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def main() -> int:
    started = datetime.now(timezone.utc).isoformat()
    before = _size(ART) + _size(EXPORT)
    removed: list[dict[str, object]] = []

    # Drop duplicated heavy trees under artifacts/run-bot.  The upload step will
    # include selected .data files directly, and the cache/save step persists the
    # real inventory state.
    for rel in ('cache', 'exports/2026-06-13', 'exports/2026-06-12'):
        _remove(ART / rel, removed)

    # Remove nested date export folders and bulky line snapshots from artifacts copy.
    for parent in [ART / 'exports', EXPORT]:
        if parent.exists():
            for child in parent.iterdir():
                if child.is_dir() and child.name.startswith('20'):
                    _remove(child, removed)
            for pattern in ('*line-snapshots*.json', '*odds_movement*.json', '*.jsonl'):
                for f in parent.glob(pattern):
                    _remove(f, removed)

    # Keep only compact day inventory and line history in the artifact copy.
    cache_date = os.getenv('DAY_INVENTORY_CACHE_DATE') or os.getenv('DAY_INVENTORY_TARGET_DATE') or ''
    for folder in (ART / 'day_inventory', ART / 'line_history'):
        if folder.exists():
            for f in folder.glob('*.json'):
                if f.name not in {'current.json', 'latest.json', 'today.json', f'{cache_date}.json'}:
                    _remove(f, removed)

    # Ensure key latest files are present in artifacts/run-bot even if earlier
    # broad copies were pruned.
    for name in KEEP_NAMES:
        _copy_file(EXPORT / name, ART / name)
    for name in ('state.json', 'published-candidate-index.json', 'fallback-sent-index.json', 'candidate-lifecycle-state.json'):
        _copy_file(ROOT / '.data' / name, ART / name)

    after = _size(ART) + _size(EXPORT)
    payload = {
        'status': 'ok',
        'started_at_utc': started,
        'finished_at_utc': datetime.now(timezone.utc).isoformat(),
        'bytes_before': before,
        'bytes_after': after,
        'bytes_removed_estimate': max(0, before - after),
        'removed_sample': removed[:80],
        'notes': [
            'Prunes upload payload only; it does not delete persistent .data/day_inventory or .data/line_history before cache/save.',
            'Large date folders and duplicate artifacts/run-bot/cache are excluded to avoid upload-artifact timeout.',
        ],
    }
    STATUS.parent.mkdir(parents=True, exist_ok=True)
    STATUS.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    print(json.dumps({k: payload[k] for k in ('status', 'bytes_before', 'bytes_after', 'bytes_removed_estimate')}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
