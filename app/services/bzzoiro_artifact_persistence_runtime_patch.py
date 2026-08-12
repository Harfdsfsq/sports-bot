from __future__ import annotations

"""Persist raw Bzzoiro provider rows for targeted A-tier matching.

Runtime patch only: wraps Bzzoiro v1/v2 fetch methods and writes the actual event
and offer rows that providers already fetched. No publication guards are changed.
"""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

EXPORT = Path('.data/exports')


def _write(name: str, payload: Any) -> None:
    try:
        EXPORT.mkdir(parents=True, exist_ok=True)
        (EXPORT / name).write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    except Exception:
        pass


def _rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if isinstance(payload, dict):
        for key in ('results', 'data', 'events', 'rows', 'items'):
            val = payload.get(key)
            if isinstance(val, list):
                return [x for x in val if isinstance(x, dict)]
    return []


def _serialize_offer(offer: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key in ('source','bookmaker','family','selection','price','point','team_side','market_name','market_key','source_event_id','metadata'):
        try:
            val = getattr(offer, key, None)
            if val not in (None, ''):
                out[key] = val
        except Exception:
            pass
    return out


def _patch_provider(module_name: str) -> dict[str, Any]:
    try:
        module = __import__(module_name, fromlist=['BzzoiroContextProvider'])
        cls = getattr(module, 'BzzoiroContextProvider')
    except Exception as exc:
        return {'module': module_name, 'status': 'missing', 'error': str(exc)}

    if getattr(cls, '_harizon_bzzoiro_artifact_persistence', False):
        return {'module': module_name, 'status': 'already_installed'}

    old_fetch_events = getattr(cls, '_fetch_events', None)
    if callable(old_fetch_events):
        async def wrapped_fetch_events(self: Any, client: Any, headers: dict[str, str], date_from: str, date_to: str, stats: dict[str, Any]) -> list[dict[str, Any]]:
            rows = await old_fetch_events(self, client, headers, date_from, date_to, stats)
            _write('latest-bzzoiro-events-raw.json', {'created_at_utc': datetime.now(UTC).isoformat(), 'provider_module': module_name, 'date_from': date_from, 'date_to': date_to, 'events': rows, 'event_count': len(rows)})
            _write('latest-bzzoiro-events.json', {'created_at_utc': datetime.now(UTC).isoformat(), 'source': 'bzzoiro', 'events': rows, 'event_count': len(rows), 'diagnosis': 'persisted_from_provider_fetch_events'})
            return rows
        cls._fetch_events = wrapped_fetch_events

    old_fetch_paginated_rows = getattr(cls, '_fetch_paginated_rows', None)
    if callable(old_fetch_paginated_rows):
        async def wrapped_fetch_paginated_rows(self: Any, client: Any, path: str, *, headers: dict[str, str], params: dict[str, Any], stats: dict[str, Any]) -> list[dict[str, Any]]:
            rows = await old_fetch_paginated_rows(self, client, path, headers=headers, params=params, stats=stats)
            if 'events' in str(path):
                _write('latest-bzzoiro-events-raw.json', {'created_at_utc': datetime.now(UTC).isoformat(), 'provider_module': module_name, 'path': path, 'params': params, 'events': rows, 'event_count': len(rows)})
                _write('latest-bzzoiro-events.json', {'created_at_utc': datetime.now(UTC).isoformat(), 'source': 'bzzoiro', 'events': rows, 'event_count': len(rows), 'diagnosis': 'persisted_from_provider_paginated_events'})
            elif 'prediction' in str(path):
                _write('latest-bzzoiro-predictions-raw.json', {'created_at_utc': datetime.now(UTC).isoformat(), 'provider_module': module_name, 'path': path, 'params': params, 'predictions': rows, 'prediction_count': len(rows)})
            return rows
        cls._fetch_paginated_rows = wrapped_fetch_paginated_rows

    old_fetch_offers = getattr(cls, 'fetch_offers', None)
    if callable(old_fetch_offers):
        async def wrapped_fetch_offers(self: Any, matches: list[Any]) -> tuple[dict[str, list[Any]], dict[str, Any], dict[str, Any]]:
            offers_by_match, stats, preview = await old_fetch_offers(self, matches)
            rows = []
            for match_key, offers in (offers_by_match or {}).items():
                for offer in offers or []:
                    item = _serialize_offer(offer)
                    item['match_key'] = match_key
                    rows.append(item)
            _write('latest-bzzoiro-odds-raw.json', {'created_at_utc': datetime.now(UTC).isoformat(), 'provider_module': module_name, 'rows': rows, 'offer_count': len(rows), 'stats': stats, 'preview': preview})
            _write('latest-bzzoiro-odds.json', {'created_at_utc': datetime.now(UTC).isoformat(), 'source': 'bzzoiro', 'rows': rows, 'offer_count': len(rows), 'diagnosis': 'persisted_from_provider_fetch_offers'})
            return offers_by_match, stats, preview
        cls.fetch_offers = wrapped_fetch_offers

    cls._harizon_bzzoiro_artifact_persistence = True
    return {'module': module_name, 'status': 'installed'}


def install() -> dict[str, Any]:
    return {'status': 'installed', 'results': [_patch_provider('app.providers.bzzoiro_v2'), _patch_provider('app.providers.bzzoiro')], 'publication_contract_relaxed': False}
