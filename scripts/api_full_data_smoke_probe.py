from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx

EXPORT = Path('.data/exports')
UTC = timezone.utc


def _rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list): return [r for r in payload if isinstance(r, dict)]
    if isinstance(payload, dict):
        for k in ('results','data','events','rows','items'):
            v = payload.get(k)
            if isinstance(v, list): return [r for r in v if isinstance(r, dict)]
    return []

async def _get(client: httpx.AsyncClient, source: str, url: str, **kwargs: Any) -> Any:
    try:
        r = await client.get(url, headers=kwargs.get('headers'), params=kwargs.get('params'))
        return r.json() if r.status_code == 200 else {'status_code': r.status_code, 'text': r.text[:500]}
    except Exception as exc:
        return {'error': f'{type(exc).__name__}: {exc}'}

async def _main() -> int:
    now = datetime.now(UTC)
    today = now.date().isoformat(); day_after = (now + timedelta(days=2)).date().isoformat()
    key = os.getenv('BZZOIRO_API_KEY')
    out: dict[str, Any] = {'created_at_utc': now.isoformat(), 'bzzoiro': {}}
    if key:
        headers = {'Authorization': f'Token {key}'}
        base = os.getenv('BZZOIRO_BASE_URL', 'https://sports.bzzoiro.com/api').rstrip('/')
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
            event_payload = await _get(client, 'bzzoiro', f'{base}/events/', headers=headers, params={'date_from': today, 'date_to': day_after, 'tz': 'UTC', 'limit': 100})
            event_rows = _rows(event_payload)
            out['bzzoiro']['events'] = len(event_rows)
            try:
                from scripts.bzzoiro_probe_row_persistence import persist_events
                persist_events(event_payload, endpoint=f'{base}/events/', artifact='api_full_data_smoke_probe')
            except Exception: pass
            pred_payload = await _get(client, 'bzzoiro', f'{base}/predictions/', headers=headers, params={'date_from': today, 'date_to': day_after, 'upcoming': 'true', 'tz': 'UTC', 'limit': 100})
            out['bzzoiro']['predictions'] = len(_rows(pred_payload))
    EXPORT.mkdir(parents=True, exist_ok=True)
    (EXPORT/'latest-api-full-data-smoke-probe.json').write_text(json.dumps(out, ensure_ascii=False, indent=2, sort_keys=True)+'\n', encoding='utf-8')
    print(json.dumps(out, ensure_ascii=False))
    return 0

def main() -> int:
    return asyncio.run(_main())

if __name__ == '__main__':
    raise SystemExit(main())
