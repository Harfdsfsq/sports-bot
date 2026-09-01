from __future__ import annotations

import json
from pathlib import Path
from scripts.count_market_sources import count_sources

EXPORT = Path('.data/exports')
OUT = EXPORT / 'latest-live-source-quorum-report.json'
ART = Path('artifacts/run-bot/latest-live-source-quorum-report.json')


def load(path, default):
    try:
        if path.exists() and path.stat().st_size > 0:
            return json.loads(path.read_text(encoding='utf-8', errors='replace'))
    except Exception:
        pass
    return default


def rows(payload):
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if isinstance(payload, dict):
        out = []
        for key in ('evaluated', 'evaluated_candidates', 'candidates', 'rows', 'selected_all'):
            value = payload.get(key)
            if isinstance(value, list):
                out.extend(x for x in value if isinstance(x, dict))
        return out
    return []


def main():
    report = load(EXPORT / 'latest-controlled-fallback-report.json', {})
    items = []
    for row in rows(report):
        items.append({
            'home_team': row.get('home_team') or row.get('home'),
            'away_team': row.get('away_team') or row.get('away'),
            'selection': row.get('selection'),
            'point': row.get('point'),
            'live_source_count': count_sources(row),
        })
    payload = {'status': 'ok', 'rows': len(items), 'items': items[:100]}
    for path in (OUT, ART):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    print(json.dumps(payload, ensure_ascii=False))
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
