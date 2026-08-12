from __future__ import annotations

"""Small helper used by smoke/probe scripts to persist Bzzoiro rows.

No publication side effects. It only writes rows that were already returned by
Bzzoiro endpoints so targeted A-tier matching can consume them.
"""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

EXPORT = Path('.data/exports')


def rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if isinstance(payload, dict):
        for key in ('results', 'data', 'events', 'rows', 'items'):
            val = payload.get(key)
            if isinstance(val, list):
                return [x for x in val if isinstance(x, dict)]
    return []


def persist_events(payload: Any, *, endpoint: str = '', artifact: str = 'probe') -> dict[str, Any]:
    event_rows = rows(payload)
    out = {
        'created_at_utc': datetime.now(UTC).isoformat(),
        'source': 'bzzoiro',
        'endpoint': endpoint,
        'artifact': artifact,
        'events': event_rows,
        'event_count': len(event_rows),
        'diagnosis': 'persisted_from_bzzoiro_probe_rows' if event_rows else 'no_event_rows_in_probe_payload',
    }
    EXPORT.mkdir(parents=True, exist_ok=True)
    (EXPORT / 'latest-bzzoiro-events-raw.json').write_text(json.dumps(out, ensure_ascii=False, indent=2, sort_keys=True)+'\n', encoding='utf-8')
    (EXPORT / 'latest-bzzoiro-events.json').write_text(json.dumps(out, ensure_ascii=False, indent=2, sort_keys=True)+'\n', encoding='utf-8')
    return out


def persist_odds(payload: Any, *, endpoint: str = '', artifact: str = 'probe') -> dict[str, Any]:
    offer_rows = rows(payload)
    out = {
        'created_at_utc': datetime.now(UTC).isoformat(),
        'source': 'bzzoiro',
        'endpoint': endpoint,
        'artifact': artifact,
        'rows': offer_rows,
        'offer_count': len(offer_rows),
        'diagnosis': 'persisted_from_bzzoiro_probe_rows' if offer_rows else 'no_offer_rows_in_probe_payload',
    }
    EXPORT.mkdir(parents=True, exist_ok=True)
    (EXPORT / 'latest-bzzoiro-odds-raw.json').write_text(json.dumps(out, ensure_ascii=False, indent=2, sort_keys=True)+'\n', encoding='utf-8')
    (EXPORT / 'latest-bzzoiro-odds.json').write_text(json.dumps(out, ensure_ascii=False, indent=2, sort_keys=True)+'\n', encoding='utf-8')
    return out
