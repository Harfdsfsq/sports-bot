from __future__ import annotations

import json
import os
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path('.').resolve()
OUT = ROOT / '.data' / 'exports' / 'latest-unsupported-market-filter.json'
DEFAULT_UNSUPPORTED_TOTAL_POINTS = {'2.25', '2.75', '3.25'}
TARGET_PATHS = [
    ROOT / '.data' / 'exports' / 'latest-rescue-candidates.json',
    ROOT / 'artifacts' / 'run-bot' / 'latest-rescue-candidates.json',
    ROOT / '.data' / 'exports' / 'latest-match-data-coverage-matches.json',
    ROOT / '.logs' / 'debug-last-run.json',
]


def env_set(name: str, default: set[str]) -> set[str]:
    raw = str(os.getenv(name) or '').strip()
    if not raw:
        return set(default)
    return {part.strip() for part in raw.split(',') if part.strip()}


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return None


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')


def as_point_text(value: Any) -> str:
    try:
        number = float(value)
        return f'{number:.2f}'.rstrip('0').rstrip('.') if number % 1 else str(int(number))
    except Exception:
        return str(value or '').strip()


def normalized_unsupported_points() -> set[str]:
    result: set[str] = set()
    for value in env_set('BOOKMAKER_UNSUPPORTED_TOTAL_POINTS', DEFAULT_UNSUPPORTED_TOTAL_POINTS):
        text = as_point_text(value)
        if text:
            result.add(text)
        try:
            result.add(f'{float(value):.2f}'.rstrip('0').rstrip('.'))
        except Exception:
            pass
    return result or set(DEFAULT_UNSUPPORTED_TOTAL_POINTS)


def family(row: dict[str, Any]) -> str:
    return str(row.get('family') or row.get('market_family') or row.get('market') or '').strip().lower()


def point(row: dict[str, Any]) -> str:
    for key in ('point', 'line', 'total', 'handicap'):
        value = row.get(key)
        if value not in (None, ''):
            return as_point_text(value)
    return ''


def is_unsupported_candidate(row: dict[str, Any], unsupported_points: set[str]) -> bool:
    fam = family(row)
    if fam not in {'totals', 'total'}:
        return False
    return point(row) in unsupported_points


def candidate_brief(row: dict[str, Any]) -> dict[str, Any]:
    return {
        'match_key': row.get('match_key') or row.get('canonical_match_id'),
        'home_team': row.get('home_team') or row.get('home'),
        'away_team': row.get('away_team') or row.get('away'),
        'family': row.get('family') or row.get('market_family') or row.get('market'),
        'selection': row.get('selection') or row.get('selection_text') or row.get('pick'),
        'point': row.get('point') or row.get('line') or row.get('total'),
        'odds': row.get('odds'),
        'reason': 'bookmaker_unsupported_total_point',
    }


def filter_payload(payload: Any, unsupported_points: set[str]) -> tuple[Any, list[dict[str, Any]], int]:
    removed: list[dict[str, Any]] = []
    scanned = 0

    def walk(value: Any) -> Any:
        nonlocal scanned
        if isinstance(value, list):
            new_items = []
            for item in value:
                if isinstance(item, dict):
                    scanned += 1
                    if is_unsupported_candidate(item, unsupported_points):
                        removed.append(candidate_brief(item))
                        continue
                new_items.append(walk(item))
            return new_items
        if isinstance(value, dict):
            # Single selected/pick object should not survive if it is unsupported.
            if is_unsupported_candidate(value, unsupported_points):
                removed.append(candidate_brief(value))
                return {
                    '_filtered_out': True,
                    'reason': 'bookmaker_unsupported_total_point',
                    'original': candidate_brief(value),
                }
            return {key: walk(item) for key, item in value.items()}
        return value

    return walk(deepcopy(payload)), removed, scanned


def main() -> int:
    unsupported_points = normalized_unsupported_points()
    report: dict[str, Any] = {
        'status': 'ok',
        'updated_at_utc': datetime.now(timezone.utc).isoformat(),
        'unsupported_total_points': sorted(unsupported_points, key=lambda item: float(item) if item.replace('.', '', 1).isdigit() else 999),
        'files': [],
        'removed_total': 0,
        'notes': [
            'Totals with these points are not supported by the bookmaker and are removed before controlled fallback publishing.',
            'This does not relax quality filters; it only blocks unsupported market lines.',
        ],
    }

    for path in TARGET_PATHS:
        payload = load_json(path)
        if payload is None:
            report['files'].append({'path': str(path), 'status': 'missing_or_invalid'})
            continue
        filtered, removed, scanned = filter_payload(payload, unsupported_points)
        if removed:
            write_json(path, filtered)
        report['files'].append(
            {
                'path': str(path),
                'status': 'updated' if removed else 'unchanged',
                'scanned_dicts': scanned,
                'removed': len(removed),
                'removed_examples': removed[:10],
            }
        )
        report['removed_total'] += len(removed)

    write_json(OUT, report)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
