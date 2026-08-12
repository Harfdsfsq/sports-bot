from __future__ import annotations

"""Trace where Bzzoiro report event counts are coming from.

This is diagnostic only. It separates aggregate counters from persisted event
rows that targeted A-tier matching can actually use.
"""

import json
from pathlib import Path
from typing import Any

EXPORT = Path('.data/exports')
OUT = EXPORT / 'latest-bzzoiro-report-source-trace.json'


def _load(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding='utf-8', errors='replace'))
    except Exception:
        return {} if default is None else default


def _int(v: Any) -> int:
    try:
        if isinstance(v, (list, tuple, set, dict)):
            return len(v)
        return int(float(v))
    except Exception:
        return 0


def _source_stats() -> tuple[dict[str, Any], list[str]]:
    scanned: list[str] = []
    for name in ('debug-last-run.json',):
        p = Path('.logs') / name
        if p.exists():
            scanned.append(str(p))
            payload = _load(p, {})
            summary = payload.get('summary') if isinstance(payload, dict) and isinstance(payload.get('summary'), dict) else {}
            stats = summary.get('source_stats') or payload.get('source_stats') or {}
            if isinstance(stats, dict) and isinstance(stats.get('bzzoiro'), dict):
                return stats.get('bzzoiro') or {}, scanned
    for name in ('latest-signal-stack-runtime.json','latest-secondary-provider-matching.json','latest-api-full-data-smoke-probe.json','latest-provider-api-min-repair-probe.json'):
        p = EXPORT / name
        if p.exists():
            scanned.append(str(p))
    return {}, scanned


def main() -> int:
    bzz, scanned = _source_stats()
    events_art = _load(EXPORT/'latest-bzzoiro-events.json', {})
    odds_art = _load(EXPORT/'latest-bzzoiro-odds.json', {})
    targeted = _load(EXPORT/'latest-bzzoiro-targeted-odds-confirmation.json', {})
    payload = {
        'status': 'ok',
        'source_stats_events_fetched': _int(bzz.get('events_fetched') or bzz.get('rows_fetched')),
        'source_stats_requests': _int(bzz.get('requests')),
        'source_stats_contexts': _int(bzz.get('contexts_built')),
        'source_stats_errors': _int(bzz.get('response_errors')),
        'persisted_event_rows': _int(events_art.get('event_count')),
        'persisted_events_diagnosis': events_art.get('diagnosis'),
        'persisted_offer_rows': _int(odds_art.get('offer_count') or odds_art.get('offer_rows')),
        'targeted_events_seen': _int(targeted.get('bzzoiro_events_seen')),
        'targeted_matched_events': _int(targeted.get('matched_events')),
        'targeted_offers': _int(targeted.get('offers')),
        'targeted_diagnosis': targeted.get('diagnosis'),
        'scanned_sources': scanned,
        'diagnosis': 'aggregate_count_without_persisted_rows' if _int(bzz.get('events_fetched') or bzz.get('rows_fetched')) and not _int(events_art.get('event_count')) else 'rows_available_or_no_aggregate',
        'publication_contract_relaxed': False,
    }
    EXPORT.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)+'\n', encoding='utf-8')
    print(json.dumps(payload, ensure_ascii=False))
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
