from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path('.')
EXPORT = ROOT / '.data' / 'exports'
OUT = ROOT / '.data' / 'calibration' / 'prediction_calibration_ledger.jsonl'
REPORT = EXPORT / 'latest-prediction-calibration-ledger.json'
ART = ROOT / 'artifacts' / 'run-bot' / 'latest-prediction-calibration-ledger.json'


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
        for key in ('published', 'selected', 'selected_all', 'evaluated', 'evaluated_candidates', 'candidates', 'rows', 'items'):
            value = payload.get(key)
            if isinstance(value, dict):
                out.append(value)
            elif isinstance(value, list):
                out.extend(x for x in value if isinstance(x, dict))
        return out
    return []


def val(row: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if row.get(key) not in (None, ''):
            return row.get(key)
    m = row.get('metrics') if isinstance(row.get('metrics'), dict) else {}
    for key in keys:
        if m.get(key) not in (None, ''):
            return m.get(key)
    return None


def key(row: dict[str, Any]) -> str:
    return '|'.join(str(val(row, x) or '') for x in ('match_key', 'home_team', 'away_team', 'family', 'selection', 'point', 'commence_time'))


def main() -> int:
    now = datetime.now(timezone.utc).isoformat()
    sources = [
        EXPORT / 'latest-controlled-fallback-report.json',
        EXPORT / 'latest-picks.json',
        EXPORT / 'latest-bets.json',
    ]
    existing: set[str] = set()
    if OUT.exists():
        for line in OUT.read_text(encoding='utf-8', errors='replace').splitlines():
            try:
                item = json.loads(line)
                existing.add(str(item.get('calibration_key') or ''))
            except Exception:
                pass
    new_rows: list[dict[str, Any]] = []
    for source in sources:
        for row in rows(load(source, [])):
            k = key(row)
            if not k or k in existing:
                continue
            item = {
                'created_at_utc': now,
                'source_file': str(source),
                'calibration_key': k,
                'home_team': val(row, 'home_team', 'home'),
                'away_team': val(row, 'away_team', 'away'),
                'league_name': val(row, 'league_name'),
                'kickoff': val(row, 'commence_time', 'kickoff', 'start_time'),
                'family': val(row, 'family', 'market_family'),
                'selection': val(row, 'selection', 'selection_key'),
                'point': val(row, 'point', 'line'),
                'odds': val(row, 'odds', 'selected_odds'),
                'adjusted_probability': val(row, 'adjusted_probability'),
                'market_probability': val(row, 'market_probability'),
                'ev_pct': val(row, 'canonical_ev_pct', 'ev_pct'),
                'edge_pp': val(row, 'canonical_edge_pp', 'edge_pp'),
                'confidence': val(row, 'confidence'),
                'quality_score': val(row, 'quality_score'),
                'quality_score_source': val(row, 'quality_score_source'),
                'tier': val(row, 'tier', 'publication_tier'),
                'status': val(row, 'status', 'publication_lifecycle_status'),
                'result_status': val(row, 'result_status', 'settlement_status'),
                'closing_odds': val(row, 'closing_odds', 'closing_price'),
                'clv_pct': val(row, 'clv_pct'),
                'profit': val(row, 'profit', 'pnl'),
            }
            new_rows.append(item)
            existing.add(k)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open('a', encoding='utf-8') as fh:
        for item in new_rows:
            fh.write(json.dumps(item, ensure_ascii=False, sort_keys=True) + '\n')
    report = {'status': 'ok', 'created_at_utc': now, 'added_rows': len(new_rows), 'ledger_path': str(OUT)}
    for path in (REPORT, ART):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
