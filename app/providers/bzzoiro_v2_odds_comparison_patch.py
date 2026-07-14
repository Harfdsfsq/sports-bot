from __future__ import annotations

"""Add Bzzoiro v2 odds-comparison details to context enrichment.

The clean v2 provider already matches /events/ and fetches event odds, stats,
metadata and prediction.  The public docs also expose
/events/{id}/odds/comparison/.  Recent runs showed 2+ context/line coverage
stalling while fallback candidates kept proxy/default xG.  This patch enriches
already matched Bzzoiro contexts with odds-comparison payloads and records a
report without changing the matching logic or publication guards.
"""

import json
import os
from pathlib import Path
from typing import Any

import httpx

OUT = Path('.data/exports/latest-bzzoiro-v2-odds-comparison-patch.json')
ART = Path('artifacts/run-bot/latest-bzzoiro-v2-odds-comparison-patch.json')


def _truthy(name: str, default: bool = True) -> bool:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == '':
        return default
    return str(raw).strip().lower() in {'1', 'true', 'yes', 'on', 'force'}


def _int_env(name: str, default: int) -> int:
    try:
        raw = os.getenv(name)
        return int(float(str(raw).strip())) if raw not in (None, '') else default
    except Exception:
        return default


def _rows(payload: Any) -> list[Any]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ('results', 'data', 'items', 'odds', 'comparisons', 'bookmakers'):
            value = payload.get(key)
            if isinstance(value, list):
                return value
    return []


def _write(payload: dict[str, Any]) -> None:
    for path in (OUT, ART):
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')
        except Exception:
            pass


def install() -> None:
    if not _truthy('BZZOIRO_V2_FETCH_ODDS_COMPARISON', True):
        return
    try:
        from app.providers import bzzoiro_v2
    except Exception:
        return
    cls = getattr(bzzoiro_v2, 'BzzoiroContextProvider', None)
    if cls is None or getattr(cls, '_harizon_odds_comparison_context_patched', False):
        return
    original = getattr(cls, 'fetch_context', None)
    if not callable(original):
        return

    async def patched_fetch_context(self, matches):
        contexts, stats, preview = await original(self, matches)
        if not contexts or not getattr(self, 'api_key', None):
            return contexts, stats, preview
        limit = max(0, _int_env('BZZOIRO_V2_ODDS_COMPARISON_MATCH_LIMIT', 180))
        max_requests = max(0, _int_env('BZZOIRO_V2_ODDS_COMPARISON_MAX_REQUESTS', limit or 180))
        if limit <= 0 or max_requests <= 0:
            return contexts, stats, preview
        headers = {'Authorization': f'Token {self.api_key}'}
        fetched = 0
        rows_total = 0
        errors = 0
        examples: list[dict[str, Any]] = []
        try:
            async with httpx.AsyncClient(timeout=getattr(self, 'timeout', 20.0), follow_redirects=True) as client:
                for match_key, ctx in list(contexts.items())[:limit]:
                    if fetched >= max_requests:
                        break
                    details = getattr(ctx, 'details', {}) if hasattr(ctx, 'details') else {}
                    payload = getattr(ctx, 'payload', {}) if hasattr(ctx, 'payload') else {}
                    event_id = None
                    if isinstance(details, dict):
                        event_id = details.get('bzzoiro_event_id')
                    if event_id in (None, '') and isinstance(payload, dict):
                        event = payload.get('event') if isinstance(payload.get('event'), dict) else {}
                        event_id = event.get('id')
                    if event_id in (None, ''):
                        continue
                    try:
                        comparison = await self._get_json(client, f'/events/{event_id}/odds/comparison/', headers, {}, stats)
                    except Exception:
                        comparison = None
                    fetched += 1
                    if comparison is None:
                        errors += 1
                        continue
                    rows = _rows(comparison)
                    rows_total += len(rows)
                    try:
                        if isinstance(payload, dict):
                            payload['odds_comparison'] = comparison
                        if isinstance(details, dict):
                            details['bzzoiro_odds_comparison_fetched'] = True
                            details['bzzoiro_odds_comparison_rows'] = len(rows)
                            details.setdefault('bzzoiro_context_sources_used', {})
                            used = details.get('bzzoiro_context_sources_used')
                            if isinstance(used, dict):
                                used['odds_comparison'] = bool(rows or comparison)
                    except Exception:
                        pass
                    if len(examples) < 10:
                        examples.append({'match_key': match_key, 'event_id': event_id, 'rows': len(rows)})
        except Exception as exc:
            errors += 1
            if isinstance(stats, dict):
                stats['bzzoiro_odds_comparison_patch_error'] = f'{type(exc).__name__}: {exc}'
        if isinstance(stats, dict):
            stats['odds_comparison_patch'] = {
                'enabled': True,
                'requests': fetched,
                'rows_total': rows_total,
                'errors': errors,
                'limit': limit,
                'max_requests': max_requests,
            }
        _write({'status': 'ok', 'requests': fetched, 'rows_total': rows_total, 'errors': errors, 'examples': examples})
        return contexts, stats, preview

    cls.fetch_context = patched_fetch_context
    cls._harizon_odds_comparison_context_patched = True
