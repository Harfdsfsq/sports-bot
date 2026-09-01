from __future__ import annotations

import asyncio
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from app.config import Settings
from app.providers.bzzoiro_v2 import BzzoiroContextProvider

ROOT = Path('.').resolve()
DAY_DIR = ROOT / '.data' / 'day_inventory'
EXPORT_DIR = ROOT / '.data' / 'exports'
POOL_PATH = EXPORT_DIR / 'latest-provider-day-discovery-canonical-pool.json'
OUT = EXPORT_DIR / 'latest-bzzoiro-pool-id-inventory-enrichment.json'
UTC = timezone.utc


def load(path: Path, default: Any) -> Any:
    try:
        if path.exists() and path.stat().st_size > 0:
            return json.loads(path.read_text(encoding='utf-8', errors='replace'))
    except Exception:
        pass
    return default


def write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')


def as_int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ''):
            return default
        return int(float(str(value)))
    except Exception:
        return default


def truthy(value: Any, default: bool = True) -> bool:
    if value in (None, ''):
        return default
    return str(value).strip().lower() in {'1', 'true', 'yes', 'on', 'force'}


def norm(value: Any) -> str:
    text = str(value or '').strip().lower().replace('ё', 'е').replace('´', "'")
    text = re.sub(r'\b(fc|sc|cf|fk|ac|cd|club|de|la|the|w|women|u19|u20|u21|ii|2)\b', ' ', text)
    text = re.sub(r'[^a-z0-9а-я]+', ' ', text)
    return ' '.join(text.split())


def row_date(row: dict[str, Any]) -> str:
    for key in ('kickoff_utc', 'commence_time', 'start_time', 'kickoff', 'date'):
        text = str(row.get(key) or '')
        m = re.search(r'(20\d{2}-\d{2}-\d{2})', text)
        if m:
            return m.group(1)
    text = str(row.get('match_key') or row.get('canonical_match_id') or '')
    m = re.search(r'(20\d{2}-\d{2}-\d{2})', text)
    return m.group(1) if m else ''


def home(row: dict[str, Any]) -> str:
    return str(row.get('home_team') or row.get('home') or row.get('home_name') or '').strip()


def away(row: dict[str, Any]) -> str:
    return str(row.get('away_team') or row.get('away') or row.get('away_name') or '').strip()


def semantic_key(row: dict[str, Any]) -> str:
    day = row_date(row)
    h = norm(home(row))
    a = norm(away(row))
    if day and h and a:
        return f'{day}|{h}|{a}'
    return norm(row.get('canonical_match_id') or row.get('match_key'))


def canonical_key(row: dict[str, Any]) -> str:
    raw = str(row.get('canonical_match_key') or '').strip()
    if raw:
        parts = raw.split('|')
        if len(parts) >= 3:
            return f'{parts[0][:10]}|{norm(parts[1])}|{norm(parts[2])}'
    day = str(row.get('kickoff_utc') or '')[:10]
    return f'{day}|{norm(row.get("home_team"))}|{norm(row.get("away_team"))}'


def items(value: Any) -> list[str]:
    if isinstance(value, dict):
        return [str(k).strip() for k in value if str(k).strip()]
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    if isinstance(value, str) and value.strip():
        return [x.strip() for x in re.split(r'[,|;/]+', value) if x.strip()]
    return []


def add_unique(row: dict[str, Any], field: str, value: str) -> bool:
    current = items(row.get(field))
    seen = {x.lower() for x in current}
    if value.lower() in seen:
        row[field] = current
        return False
    current.append(value)
    row[field] = current
    return True


def count_sources(row: dict[str, Any], field: str) -> int:
    return len(items(row.get(field)))


def needs_bzz(row: dict[str, Any]) -> bool:
    odds = {x.lower() for x in items(row.get('odds_sources')) + items(row.get('line_sources'))}
    ctx = {x.lower() for x in items(row.get('context_sources')) + items(row.get('context_confirmations'))}
    return ('bzzoiro' not in ctx and len(ctx) < 2) or ('bzzoiro' not in odds and len(odds & {'odds_api_io', 'bzzoiro', 'sportlogic'}) < 2)


def pool_index() -> dict[str, list[dict[str, Any]]]:
    pool = load(POOL_PATH, {})
    rows = pool.get('canonical_matches') if isinstance(pool.get('canonical_matches'), list) else []
    out: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        k = canonical_key(row)
        if not k:
            continue
        events = []
        for ev in row.get('source_events') or []:
            if isinstance(ev, dict) and str(ev.get('provider') or '').lower() == 'bzzoiro' and ev.get('source_id') not in (None, ''):
                events.append(ev)
        if events:
            out[k] = events
    return out


def target_day() -> str:
    return str(os.getenv('DAY_INVENTORY_TARGET_DATE') or os.getenv('DAY_INVENTORY_CACHE_DATE') or datetime.now(UTC).date().isoformat())[:10]


def inventory_payload() -> tuple[str, dict[str, Any]]:
    day = target_day()
    for path in (DAY_DIR / f'{day}.json', DAY_DIR / 'latest.json', DAY_DIR / 'current.json', DAY_DIR / 'today.json'):
        payload = load(path, {})
        if isinstance(payload, dict) and isinstance(payload.get('matches'), list):
            return day, payload
    return day, {'matches': [], 'counts': {}}


def comparison_hints(payload: Any) -> list[dict[str, Any]]:
    try:
        from app.services import bzzoiro_odds_comparison_bridge_patch as bridge  # type: ignore
        hints = bridge._enhanced_bzzoiro_odds_hints(payload)  # type: ignore[attr-defined]
        return [x for x in hints if isinstance(x, dict)]
    except Exception:
        return []


async def get_json(provider: BzzoiroContextProvider, client: httpx.AsyncClient, path: str, stats: dict[str, Any]) -> Any:
    token = provider.api_key or os.getenv('BZZOIRO_API_KEY') or ''
    if not token:
        return None
    return await provider._get_json(client, path, {'Authorization': f'Token {token}'}, {}, stats)


async def run() -> dict[str, Any]:
    if not truthy(os.getenv('BZZOIRO_POOL_ID_INVENTORY_ENRICHMENT_ENABLED'), True):
        return {'status': 'disabled'}
    index = pool_index()
    day, payload = inventory_payload()
    rows = payload.get('matches') if isinstance(payload.get('matches'), list) else []
    if not rows:
        return {'status': 'no_inventory', 'pool_keys': len(index)}
    provider = BzzoiroContextProvider(Settings())
    if not provider.api_key:
        return {'status': 'missing_api_key', 'pool_keys': len(index), 'inventory_rows': len(rows)}
    limit = max(1, as_int(os.getenv('BZZOIRO_POOL_ID_ENRICHMENT_LIMIT'), 80))
    stats = {'requests': 0, 'response_errors': 0, 'retry_attempts': 0, 'http_statuses': [], 'payload_shapes': [], 'last_url': None, 'last_error': None, 'last_body_preview': None}
    touched = 0
    targets = 0
    hydrated = 0
    hints_added = 0
    examples: list[dict[str, Any]] = []
    seen_event_ids: set[str] = set()
    async with httpx.AsyncClient(timeout=provider.timeout, follow_redirects=True) as client:
        for row in rows:
            if not isinstance(row, dict) or not needs_bzz(row):
                continue
            events = index.get(semantic_key(row)) or []
            if not events:
                continue
            targets += 1
            event = events[0]
            event_id = str(event.get('source_id') or '').strip()
            if not event_id or event_id in seen_event_ids:
                continue
            seen_event_ids.add(event_id)
            if len(seen_event_ids) > limit:
                break
            detail_event = await get_json(provider, client, f'/events/{event_id}/', stats) or event
            details = {'event': detail_event if isinstance(detail_event, dict) else event, 'odds': None, 'stats': None, 'metadata': None, 'prediction': None}
            details['odds'] = await get_json(provider, client, f'/events/{event_id}/odds/', stats)
            details['stats'] = await get_json(provider, client, f'/events/{event_id}/stats/', stats)
            details['metadata'] = await get_json(provider, client, f'/events/{event_id}/metadata/', stats)
            details['prediction'] = await get_json(provider, client, f'/events/{event_id}/prediction/', stats)
            comparison = await get_json(provider, client, f'/events/{event_id}/odds/comparison/', stats)
            if isinstance(comparison, dict):
                details['odds_comparison'] = comparison
                details['comparison'] = comparison
            ctx = provider._event_to_context(details, 'exact')
            if ctx is None:
                continue
            before = json.dumps(row, ensure_ascii=False, sort_keys=True, default=str)
            add_unique(row, 'context_sources', 'bzzoiro')
            add_unique(row, 'context_confirmations', 'bzzoiro')
            row['context_sources_count'] = max(as_int(row.get('context_sources_count')), count_sources(row, 'context_sources'))
            row['has_context'] = True
            if ctx.expected_home is not None:
                row['expected_home'] = ctx.expected_home
            if ctx.expected_away is not None:
                row['expected_away'] = ctx.expected_away
            hints = comparison_hints(comparison)
            if hints:
                add_unique(row, 'odds_sources', 'bzzoiro')
                add_unique(row, 'line_sources', 'bzzoiro')
                row['odds_sources_count'] = max(as_int(row.get('odds_sources_count')), len({x for x in items(row.get('odds_sources')) if x in {'odds_api_io', 'bzzoiro', 'sportlogic'}}))
                hints_added += len(hints)
            ss = row.setdefault('source_summary', {}) if isinstance(row.setdefault('source_summary', {}), dict) else {}
            ss['bzzoiro_pool_id_enriched'] = True
            ss['bzzoiro_pool_event_id'] = event_id
            ss['bzzoiro_pool_hints_count'] = len(hints)
            ss['bzzoiro_pool_context_confidence'] = ctx.confidence
            if json.dumps(row, ensure_ascii=False, sort_keys=True, default=str) != before:
                touched += 1
                hydrated += 1
                if len(examples) < 15:
                    examples.append({'event_id': event_id, 'home': home(row), 'away': away(row), 'expected_home': row.get('expected_home'), 'expected_away': row.get('expected_away'), 'odds_sources': row.get('odds_sources'), 'context_sources': row.get('context_sources'), 'hints': len(hints)})
    payload['matches'] = rows
    payload['bzzoiro_pool_id_updated_at_utc'] = datetime.now(UTC).isoformat()
    for path in (DAY_DIR / f'{day}.json', DAY_DIR / 'latest.json', DAY_DIR / 'current.json', DAY_DIR / 'today.json'):
        write(path, payload)
    report = {'status': 'ok', 'created_at_utc': datetime.now(UTC).isoformat(), 'pool_keys': len(index), 'inventory_rows': len(rows), 'targets_with_pool_id': targets, 'event_ids_hydrated': len(seen_event_ids), 'rows_touched': touched, 'contexts_added': hydrated, 'odds_hints_added': hints_added, 'stats': stats, 'examples': examples}
    write(OUT, report)
    return report


def main() -> int:
    report = asyncio.run(run())
    write(OUT, report)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
