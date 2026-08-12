from __future__ import annotations

"""Trace Bzzoiro aggregate counters vs usable persisted rows.

Diagnostic only. If the main runner wrote only source_stats plus source_previews,
this script persists preview rows as a limited artifact and marks them as preview
so A-tier diagnostics stop treating aggregate counters as usable full rows.
"""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

EXPORT = Path('.data/exports')
OUT = EXPORT / 'latest-bzzoiro-report-source-trace.json'


def _load(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding='utf-8', errors='replace'))
    except Exception:
        return {} if default is None else default


def _write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')


def _int(v: Any) -> int:
    try:
        if isinstance(v, (list, tuple, set, dict)):
            return len(v)
        return int(float(v))
    except Exception:
        return 0


def _first_dict(*values: Any) -> dict[str, Any]:
    for value in values:
        if isinstance(value, dict):
            return value
    return {}


def _source_stats(debug: dict[str, Any]) -> dict[str, Any]:
    summary = _first_dict(debug.get('summary'))
    stats = _first_dict(summary.get('source_stats'), debug.get('source_stats'))
    return _first_dict(stats.get('bzzoiro'))


def _preview_rows(debug: dict[str, Any]) -> list[dict[str, Any]]:
    previews = _first_dict(debug.get('source_previews'))
    bzz = _first_dict(previews.get('bzzoiro'))
    candidates: list[Any] = []
    for key in ('sample_events', 'events', 'matched_examples', 'sample_predictions'):
        val = bzz.get(key)
        if isinstance(val, list):
            candidates.extend(val)
    rows = [x for x in candidates if isinstance(x, dict)]
    # Prefer actual event-like rows over matched examples when available.
    eventish = [r for r in rows if r.get('home_team') or r.get('away_team') or r.get('home') or r.get('away')]
    return eventish or rows


def _persist_preview_events(rows: list[dict[str, Any]], aggregate: int) -> None:
    if not rows:
        return
    payload = {
        'created_at_utc': datetime.now(UTC).isoformat(),
        'source': 'bzzoiro',
        'events': rows,
        'event_count': len(rows),
        'aggregate_event_count_from_source_stats': aggregate,
        'preview_only': True,
        'diagnosis': 'persisted_from_debug_source_previews_limited_sample',
    }
    _write(EXPORT / 'latest-bzzoiro-events-preview.json', payload)
    # Only populate the canonical event artifact from preview rows when there is
    # no stronger full-row artifact. Mark preview_only so downstream diagnostics
    # do not mistake samples for the full 177-row universe.
    existing = _load(EXPORT / 'latest-bzzoiro-events.json', {})
    if not _int(existing.get('event_count')):
        _write(EXPORT / 'latest-bzzoiro-events.json', payload)


def main() -> int:
    debug = _load(Path('.logs/debug-last-run.json'), {})
    bzz = _source_stats(debug if isinstance(debug, dict) else {})
    aggregate_events = _int(bzz.get('events_fetched') or bzz.get('rows_fetched'))
    preview_rows = _preview_rows(debug if isinstance(debug, dict) else {})
    events_art_before = _load(EXPORT / 'latest-bzzoiro-events.json', {})
    if preview_rows and not _int(events_art_before.get('event_count')):
        _persist_preview_events(preview_rows, aggregate_events)
    events_art = _load(EXPORT / 'latest-bzzoiro-events.json', {})
    odds_art = _load(EXPORT / 'latest-bzzoiro-odds.json', {})
    targeted = _load(EXPORT / 'latest-bzzoiro-targeted-odds-confirmation.json', {})
    persisted_rows = _int(events_art.get('event_count'))
    preview_only = bool(events_art.get('preview_only'))
    if aggregate_events and not persisted_rows:
        diagnosis = 'aggregate_count_without_persisted_rows'
    elif preview_only:
        diagnosis = 'aggregate_count_with_preview_sample_only'
    elif persisted_rows:
        diagnosis = 'full_or_provider_rows_available'
    else:
        diagnosis = 'no_bzzoiro_event_evidence'
    payload = {
        'status': 'ok',
        'source_stats_events_fetched': aggregate_events,
        'source_stats_requests': _int(bzz.get('requests')),
        'source_stats_contexts': _int(bzz.get('contexts_built')),
        'source_stats_errors': _int(bzz.get('response_errors')),
        'debug_preview_rows': len(preview_rows),
        'persisted_event_rows': persisted_rows,
        'persisted_events_preview_only': preview_only,
        'persisted_events_diagnosis': events_art.get('diagnosis'),
        'persisted_offer_rows': _int(odds_art.get('offer_count') or odds_art.get('offer_rows')),
        'targeted_events_seen': _int(targeted.get('bzzoiro_events_seen')),
        'targeted_matched_events': _int(targeted.get('matched_events')),
        'targeted_offers': _int(targeted.get('offers')),
        'targeted_diagnosis': targeted.get('diagnosis'),
        'scanned_sources': ['.logs/debug-last-run.json', '.logs/debug-last-run.json/source_previews.bzzoiro', '.data/exports/latest-bzzoiro-events.json'],
        'diagnosis': diagnosis,
        'publication_contract_relaxed': False,
    }
    _write(OUT, payload)
    print(json.dumps(payload, ensure_ascii=False))
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
