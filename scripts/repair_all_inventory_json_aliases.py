from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path('.')
OUT = ROOT / '.data' / 'exports' / 'latest-all-inventory-json-alias-repair.json'
MARKERS = ('<<<<<<<', '=======', '>>>>>>>')


def score(payload: Any) -> int:
    if not isinstance(payload, dict):
        return 0
    rows = payload.get('matches') if isinstance(payload.get('matches'), list) else []
    counts = payload.get('counts') if isinstance(payload.get('counts'), dict) else {}
    total = len(rows) * 10
    for key in ('matches_total', 'target_matches'):
        try:
            total += int(float(str(counts.get(key) or payload.get(key) or 0)))
        except Exception:
            pass
    if payload.get('status') == 'ok' or payload.get('build_status') == 'ok':
        total += 25
    return total


def variants(text: str) -> list[str]:
    if not any(m in text for m in MARKERS):
        return [text]
    pattern = re.compile(r'(?ms)^(<<<<<<< [^\n]*\n)(.*?)(^=======\n)(.*?)(^>>>>>>> [^\n]*\n?)')
    out = []
    for side in (2, 4):
        candidate = text
        while True:
            m = pattern.search(candidate)
            if not m:
                break
            candidate = candidate[:m.start()] + m.group(side) + candidate[m.end():]
        out.append(candidate)
    return out


def repair(path: Path) -> dict[str, Any]:
    result = {'path': str(path), 'exists': path.exists(), 'changed': False, 'status': 'missing'}
    if not path.exists() or not path.is_file():
        return result
    text = path.read_text(encoding='utf-8', errors='replace')
    has_markers = any(m in text for m in MARKERS)
    try:
        json.loads(text)
        if not has_markers:
            return {**result, 'status': 'ok'}
    except Exception as exc:
        result['initial_error'] = f'{type(exc).__name__}: {exc}'
    best = None
    for idx, variant in enumerate(variants(text)):
        try:
            payload = json.loads(variant)
        except Exception:
            continue
        current = (score(payload), idx, payload)
        if best is None or current[0] > best[0]:
            best = current
    if best is None:
        payload = {'status': 'repaired_empty', 'matches': [], 'counts': {'matches_total': 0}, 'repaired_at_utc': datetime.now(timezone.utc).isoformat()}
        selected = 'empty'
    else:
        payload = best[2]
        selected = f'variant_{best[1] + 1}'
    try:
        path.with_suffix(path.suffix + '.broken.bak').write_text(text, encoding='utf-8')
    except Exception:
        pass
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    return {**result, 'status': 'repaired', 'changed': True, 'had_conflict_markers': has_markers, 'selected': selected, 'score': score(payload)}


def candidate_paths() -> list[Path]:
    paths: list[Path] = []
    for base in (ROOT / '.data' / 'day_inventory', ROOT / '.data' / 'cache' / 'day_inventory'):
        if base.exists():
            paths.extend(sorted(base.glob('*.json')))
    paths.append(ROOT / '.data' / 'exports' / 'latest-day-inventory-summary.json')
    seen = set()
    out = []
    for path in paths:
        key = str(path)
        if key not in seen:
            seen.add(key)
            out.append(path)
    return out


def main() -> int:
    items = [repair(path) for path in candidate_paths()]
    payload = {'status': 'ok', 'updated_at_utc': datetime.now(timezone.utc).isoformat(), 'changed_count': sum(1 for x in items if x.get('changed')), 'items': items}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
