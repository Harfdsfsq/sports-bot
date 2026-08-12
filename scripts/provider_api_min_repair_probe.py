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

async def _main() -> int:
    now = datetime.now(UTC); today = now.date().isoformat(); tomorrow = (now + timedelta(days=1)).date().isoformat()
    report: dict[str, Any] = {'created_at_utc': now.isoformat(), 'bzzoiro': {'events': 0, 'persisted_rows': 0}}
    key = os.getenv('BZZOIRO_API_KEY')
    if key:
        base = os.getenv('BZZOIRO_BASE_URL', 'https://sports.bzzoiro.com/api').rstrip('/')
        headers = {'Authorization': f'Token {key}'}
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            try:
                resp = await client.get(f'{base}/events/', headers=headers, params={'date_from': today, 'date_to': tomorrow, 'tz': 'UTC', 'limit': 100})
                payload = resp.json() if resp.status_code == 200 else {}
            except Exception:
                payload = {}
        rows = _rows(payload)
        report['bzzoiro']['events'] = len(rows)
        try:
            from scripts.bzzoiro_probe_row_persistence import persist_events
            persisted = persist_events(payload, endpoint=f'{base}/events/', artifact='provider_api_min_repair_probe')
            report['bzzoiro']['persisted_rows'] = int(persisted.get('event_count') or 0)
        except Exception: pass
    EXPORT.mkdir(parents=True, exist_ok=True)
    (EXPORT/'latest-provider-api-min-repair-probe.json').write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)+'\n', encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False))
    return 0

def main() -> int:
    return asyncio.run(_main())

if __name__ == '__main__': raise SystemExit(main())
