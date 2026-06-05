from __future__ import annotations

"""Persist parsed odds-api.io offers in a flat, backfill-friendly format.

The bookmaker-quorum backfill cannot infer same-side bookmaker coverage from the
provider aggregate numbers alone.  This runtime patch snapshots the actual
Offer rows returned by OddsApiIoProvider.fetch_offers, with stable match/event
identity fields, so scripts/backfill_odds_api_bookmaker_quorum_mapping.py can
map them back into frozen day-inventory coverage truth without another API call.

It does not create or modify offers and it does not affect publication guards.
"""

import json
import os
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

EXPORT_DIR = Path('.data/exports')
SNAPSHOT_PATH = EXPORT_DIR / 'latest-odds-api-io-offer-snapshot.json'
STATUS_PATH = EXPORT_DIR / 'latest-odds-api-io-offer-snapshot-install.json'


def _as_float(value: Any) -> float | None:
    try:
        if value in (None, ''):
            return None
        return float(str(value).replace(',', '.'))
    except Exception:
        return None


def _json_safe(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items() if k not in {'raw_event', 'raw_payload'}}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(v) for v in list(value)[:50]]
    if hasattr(value, 'isoformat'):
        try:
            return value.isoformat()
        except Exception:
            pass
    return str(value)


def _write_json(path: Path, payload: Any) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    except Exception:
        pass


def _side(selection: Any, market_name: Any = '') -> str:
    text = f'{selection or ""} {market_name or ""}'.lower()
    if any(token in text for token in ('under', 'меньше', 'тм')):
        return 'under'
    if any(token in text for token in ('over', 'больше', 'тб')):
        return 'over'
    return ''


def _row_from_offer(match: Any, offer: Any) -> dict[str, Any]:
    metadata = getattr(offer, 'metadata', None)
    if not isinstance(metadata, dict):
        metadata = {}
    commence = getattr(match, 'commence_time', None)
    try:
        kickoff_utc = commence.isoformat() if commence is not None else ''
    except Exception:
        kickoff_utc = str(commence or '')
    source_event_id = (
        getattr(offer, 'source_event_id', None)
        or getattr(match, 'source_event_id', None)
        or metadata.get('event_id')
        or metadata.get('source_event_id')
        or metadata.get('odds_api_io_id')
    )
    price = _as_float(getattr(offer, 'price', None))
    point = _as_float(getattr(offer, 'point', None))
    family = str(getattr(offer, 'family', '') or '')
    selection = str(getattr(offer, 'selection', '') or '')
    market_name = str(getattr(offer, 'market_name', '') or '')
    return {
        'source': 'odds_api_io',
        'provider': 'odds_api_io',
        'api': 'odds_api_io',
        'match_key': str(getattr(match, 'match_key', '') or ''),
        'canonical_match_id': str(getattr(match, 'match_key', '') or ''),
        'event_id': str(source_event_id or ''),
        'source_event_id': str(source_event_id or ''),
        'sport_key': str(getattr(match, 'sport_key', '') or 'soccer'),
        'league_name': str(getattr(match, 'league_name', '') or ''),
        'home_team': str(getattr(match, 'home_team', '') or ''),
        'away_team': str(getattr(match, 'away_team', '') or ''),
        'kickoff_utc': kickoff_utc,
        'commence_time': kickoff_utc,
        'bookmaker': str(getattr(offer, 'bookmaker', '') or ''),
        'book': str(getattr(offer, 'bookmaker', '') or ''),
        'family': family,
        'market_family': family,
        'market_name': market_name,
        'market_key': str(getattr(offer, 'market_key', '') or ''),
        'selection': selection,
        'selection_key': selection,
        'side': _side(selection, market_name),
        'point': point,
        'price': price,
        'odds': price,
        'decimal_odds': price,
        'team_side': getattr(offer, 'team_side', None),
        'odds_api_io_account': str(metadata.get('odds_api_io_account') or ''),
        'requested_bookmakers': str(metadata.get('requested_bookmakers') or ''),
        'metadata': _json_safe(metadata),
    }


def _write_snapshot(matches: list[Any], offers_by_match: dict[str, list[Any]], stats: dict[str, Any]) -> None:
    match_by_key = {str(getattr(match, 'match_key', '') or ''): match for match in matches or []}
    rows: list[dict[str, Any]] = []
    by_match: dict[str, dict[str, Any]] = {}
    by_market_books: dict[str, set[str]] = defaultdict(set)
    for match_key, offers in (offers_by_match or {}).items():
        match = match_by_key.get(str(match_key))
        if match is None:
            continue
        for offer in offers or []:
            row = _row_from_offer(match, offer)
            if not row.get('bookmaker') or not row.get('price'):
                continue
            rows.append(row)
            mkey = str(row.get('match_key') or '')
            bucket = f"{row.get('family') or ''}|{row.get('side') or ''}|{row.get('point') or ''}"
            if row.get('side') and row.get('point') is not None:
                by_market_books[f'{mkey}::{bucket}'].add(str(row.get('bookmaker') or '').strip())
            summary = by_match.setdefault(mkey, {
                'match_key': mkey,
                'home_team': row.get('home_team'),
                'away_team': row.get('away_team'),
                'kickoff_utc': row.get('kickoff_utc'),
                'books': set(),
                'offers': 0,
                'best_same_side_books': 0,
            })
            summary['books'].add(str(row.get('bookmaker') or '').strip())
            summary['offers'] += 1
    for key, books in by_market_books.items():
        match_key = key.split('::', 1)[0]
        if match_key in by_match:
            by_match[match_key]['best_same_side_books'] = max(int(by_match[match_key].get('best_same_side_books') or 0), len({b for b in books if b}))
    flat_by_match = []
    for item in by_match.values():
        books = sorted({b for b in item.pop('books', set()) if b})
        item['books'] = books
        item['books_count'] = len(books)
        flat_by_match.append(item)
    payload = {
        'status': 'ok',
        'created_at_utc': datetime.now(timezone.utc).isoformat(),
        'provider': 'odds_api_io',
        'policy': 'parsed_offer_snapshot_for_bookmaker_quorum_backfill',
        'rows_count': len(rows),
        'matches_count': len(by_match),
        'matches_with_2plus_books_any_market': sum(1 for item in flat_by_match if int(item.get('books_count') or 0) >= 2),
        'matches_with_2plus_books_same_side_market': sum(1 for item in flat_by_match if int(item.get('best_same_side_books') or 0) >= 2),
        'stats': {
            'events_matched': int(stats.get('events_matched') or 0) if isinstance(stats, dict) else 0,
            'offers_parsed': int(stats.get('offers_parsed') or 0) if isinstance(stats, dict) else 0,
            'matches_with_2plus_books': int(stats.get('matches_with_2plus_books') or 0) if isinstance(stats, dict) else 0,
            'bookmakers_seen': int(stats.get('bookmakers_seen') or 0) if isinstance(stats, dict) else 0,
        },
        'by_match': sorted(flat_by_match, key=lambda x: (-int(x.get('best_same_side_books') or 0), str(x.get('kickoff_utc') or ''), str(x.get('match_key') or '')))[:500],
        'offers': rows[:20000],
        'truncated': len(rows) > 20000,
    }
    _write_json(SNAPSHOT_PATH, payload)


class odds_api_io_offer_snapshot_runtime_patch:
    @staticmethod
    def install() -> dict[str, Any]:
        if str(os.getenv('ODDS_API_IO_OFFER_SNAPSHOT_ENABLED', 'true')).strip().lower() in {'0', 'false', 'no', 'off'}:
            status = {'status': 'disabled'}
            _write_json(STATUS_PATH, status)
            return status
        try:
            from app.providers.odds_api_io import OddsApiIoProvider
        except Exception as exc:
            status = {'status': 'error', 'stage': 'import', 'error': f'{type(exc).__name__}: {exc}'}
            _write_json(STATUS_PATH, status)
            return status
        if getattr(OddsApiIoProvider.fetch_offers, '_harizon_offer_snapshot_wrapped', False):
            status = {'status': 'already_installed'}
            _write_json(STATUS_PATH, status)
            return status
        original = OddsApiIoProvider.fetch_offers

        async def wrapped(self: Any, matches: list[Any]):
            result = await original(self, matches)
            try:
                offers_by_match, stats, _preview = result
                if isinstance(offers_by_match, dict):
                    _write_snapshot(matches, offers_by_match, stats if isinstance(stats, dict) else {})
            except Exception as exc:
                _write_json(SNAPSHOT_PATH, {
                    'status': 'error',
                    'created_at_utc': datetime.now(timezone.utc).isoformat(),
                    'error': f'{type(exc).__name__}: {exc}',
                })
            return result

        setattr(wrapped, '_harizon_offer_snapshot_wrapped', True)
        OddsApiIoProvider.fetch_offers = wrapped
        status = {'status': 'installed', 'snapshot_path': str(SNAPSHOT_PATH), 'policy': 'write_parsed_odds_api_io_offer_rows'}
        _write_json(STATUS_PATH, status)
        return status
