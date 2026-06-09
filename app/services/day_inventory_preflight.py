from __future__ import annotations

"""Preflight repair for persisted runtime JSON artifacts.

GitHub Actions stores `.data/day_inventory` and `.data/line_history` through both
commits and caches.  If a previous conflict marker reaches the repository, the
runner later reads invalid JSON and silently loses the day inventory.  This
module repairs only JSON files that are objectively broken: files containing
Git conflict markers or files that fail `json.loads`.
"""

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path('.').resolve()
EXPORT_DIR = ROOT / '.data' / 'exports'
REPORT_PATH = EXPORT_DIR / 'latest-runtime-json-preflight-repair.json'
CONFLICT_RE = re.compile(r'(?ms)^<<<<<<< .*?^=======$.*?^>>>>>>> .*?$')
MARKERS = ('<<<<<<<', '=======', '>>>>>>>')

CANDIDATE_FILES = (
    '.data/day_inventory/current.json',
    '.data/day_inventory/latest.json',
    '.data/cache/day_inventory/current.json',
    '.data/cache/day_inventory/latest.json',
    '.data/cache/day_inventory/today.json',
    '.data/exports/latest-day-inventory-summary.json',
)


def _json_score(value: Any) -> int:
    if not isinstance(value, dict):
        return 0
    matches = value.get('matches')
    counts = value.get('counts') if isinstance(value.get('counts'), dict) else {}
    score = 0
    if isinstance(matches, list):
        score += len(matches) * 10
    for key in ('matches_total', 'day_inventory_total', 'total', 'matched', 'offers'):
        try:
            score += int(float(str(counts.get(key) if counts else value.get(key) or 0)))
        except Exception:
            pass
    if value.get('build_status') == 'ok' or value.get('status') == 'ok':
        score += 25
    if value.get('sources') or value.get('all_source_match_counts'):
        score += 15
    return score


def _split_conflict_variants(text: str) -> list[str]:
    if not any(marker in text for marker in MARKERS):
        return [text]
    variants: list[str] = []
    pattern = re.compile(r'(?ms)^(<<<<<<< [^\n]*\n)(.*?)(^=======\n)(.*?)(^>>>>>>> [^\n]*\n?)')
    for side in (2, 4):
        candidate = text
        while True:
            match = pattern.search(candidate)
            if not match:
                break
            candidate = candidate[:match.start()] + match.group(side) + candidate[match.end():]
        variants.append(candidate)
    return variants


def _load_best_json_variant(text: str) -> tuple[Any | None, str | None]:
    best: tuple[int, Any, str] | None = None
    for idx, variant in enumerate(_split_conflict_variants(text)):
        try:
            payload = json.loads(variant)
        except Exception:
            continue
        score = _json_score(payload)
        if best is None or score > best[0]:
            best = (score, payload, f'conflict_side_{idx + 1}' if len(_split_conflict_variants(text)) > 1 else 'original')
    if best is None:
        return None, None
    return best[1], best[2]


def _empty_payload(path: Path) -> dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    if 'day_inventory' in str(path):
        return {
            'status': 'repaired_empty',
            'build_status': 'needs_rebuild',
            'matches': [],
            'counts': {'matches_total': 0},
            'repaired_at_utc': now,
            'repair_reason': 'invalid_json_no_parseable_conflict_side',
        }
    return {
        'status': 'repaired_empty',
        'repaired_at_utc': now,
        'repair_reason': 'invalid_json_no_parseable_conflict_side',
    }


def repair_json_file(path: Path) -> dict[str, Any]:
    result = {'path': str(path), 'exists': path.exists(), 'changed': False, 'status': 'missing'}
    if not path.exists() or not path.is_file():
        return result
    text = path.read_text(encoding='utf-8', errors='replace')
    has_conflict = any(marker in text for marker in MARKERS)
    try:
        json.loads(text)
        if not has_conflict:
            return {**result, 'status': 'ok'}
    except Exception as exc:
        result['initial_error'] = f'{type(exc).__name__}: {exc}'
    payload, source = _load_best_json_variant(text)
    if payload is None:
        payload = _empty_payload(path)
        source = 'empty_repair_payload'
    backup = path.with_suffix(path.suffix + f'.broken-{datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")}.bak')
    try:
        backup.write_text(text, encoding='utf-8')
    except Exception:
        backup = None
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    return {
        **result,
        'status': 'repaired',
        'changed': True,
        'had_conflict_markers': has_conflict,
        'selected_variant': source,
        'backup_path': str(backup) if backup else None,
        'score': _json_score(payload),
    }


def repair_runtime_json_files(paths: list[str] | None = None) -> dict[str, Any]:
    selected = paths or list(CANDIDATE_FILES)
    items = [repair_json_file(ROOT / item) for item in selected]
    payload = {
        'status': 'ok',
        'updated_at_utc': datetime.now(timezone.utc).isoformat(),
        'changed_count': sum(1 for item in items if item.get('changed')),
        'items': items,
    }
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    return payload


if __name__ == '__main__':
    print(json.dumps(repair_runtime_json_files(), ensure_ascii=False, indent=2, sort_keys=True))
