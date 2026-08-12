from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

EXPORT = Path('.data/exports')

def _load(path: Path) -> Any:
    try: return json.loads(path.read_text(encoding='utf-8', errors='replace'))
    except Exception: return {}

def _write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True); path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)+'\n', encoding='utf-8')

def _int(v: Any) -> int:
    try: return int(float(v))
    except Exception: return 0

def main() -> int:
    # Keep old role: repair report metrics, but also make the rows/artifact split explicit.
    events_art = _load(EXPORT/'latest-bzzoiro-events.json')
    persisted_rows = _int(events_art.get('event_count')) if isinstance(events_art, dict) else 0
    txt = ''
    try: txt = (EXPORT/'latest-harizon-telegram-run-report.txt').read_text(encoding='utf-8', errors='replace')
    except Exception: pass
    aggregate_events = 0
    m = re.search(r'bzzoiro: req \d+, ctx \d+, events (\d+)', txt, re.I)
    if m: aggregate_events = int(m.group(1))
    report = {'status': 'ok', 'aggregate_events_from_report': aggregate_events, 'persisted_event_rows': persisted_rows, 'diagnosis': 'aggregate_only_no_rows' if aggregate_events and not persisted_rows else 'rows_available', 'publication_contract_relaxed': False}
    _write(EXPORT/'latest-bzzoiro-v2-report-metrics-repair.json', report)
    print(json.dumps(report, ensure_ascii=False))
    return 0

if __name__ == '__main__': raise SystemExit(main())
