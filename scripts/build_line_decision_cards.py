from __future__ import annotations

import json
from pathlib import Path
from typing import Any

EXPORT = Path('.data/exports')
OUT = EXPORT / 'latest-line-decision-cards.json'
ART = Path('artifacts/run-bot/latest-line-decision-cards.json')


def load(path: Path, default: Any) -> Any:
    try:
        if path.exists() and path.stat().st_size > 0:
            return json.loads(path.read_text(encoding='utf-8', errors='replace'))
    except Exception:
        pass
    return default


def rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if isinstance(payload, dict):
        out: list[dict[str, Any]] = []
        for key in ('items', 'rows', 'candidates', 'rejected', 'dropped', 'kept', 'delayed', 'events', 'sample'):
            value = payload.get(key)
            if isinstance(value, list):
                out.extend(x for x in value if isinstance(x, dict))
        return out
    return []


def v(row: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if row.get(key) not in (None, ''):
            return row.get(key)
    for box in ('metrics', 'line_movement_guard', 'diagnostics'):
        m = row.get(box) if isinstance(row.get(box), dict) else {}
        for key in keys:
            if m.get(key) not in (None, ''):
                return m.get(key)
    return None


def card(row: dict[str, Any], source: str) -> dict[str, Any]:
    return {
        'source': source,
        'home_team': v(row, 'home_team', 'home'),
        'away_team': v(row, 'away_team', 'away'),
        'selection': v(row, 'selection', 'selection_key'),
        'point': v(row, 'point', 'line'),
        'status': v(row, 'status', 'line_movement_lifecycle_status', 'market_move'),
        'reason': v(row, 'reason', 'reject_reason', 'drop_reason', 'awaiting_reason'),
        'snapshots': v(row, 'snapshot_count', 'snapshots'),
        'edge_pp': v(row, 'edge_pp', 'canonical_edge_pp', 'current_edge_pp'),
        'ev_pct': v(row, 'ev_pct', 'canonical_ev_pct', 'current_ev_pct'),
        'next_run_available': v(row, 'next_run_available', 'has_next_run', 'has_next_regular_run_before_kickoff'),
        'no_more_run_before_kickoff': v(row, 'no_more_regular_run_before_kickoff', 'no_more_cron_before_kickoff'),
    }


def main() -> int:
    cards = []
    for row in rows(load(EXPORT / 'latest-line-movement-guard-report.json', {})):
        cards.append(card(row, 'line_guard_report'))
    for row in rows(load(EXPORT / 'latest-awaiting-movement-candidates.json', {})):
        cards.append(card(row, 'awaiting_movement'))
    payload = {'status': 'ok', 'cards_count': len(cards), 'cards': cards[:120]}
    for path in (OUT, ART):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    print(json.dumps({'status': 'ok', 'cards_count': len(cards)}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
