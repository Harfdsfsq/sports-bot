from __future__ import annotations

"""Plan low-quota price backfill for the daily top inventory.

This script does not call external APIs.  It reads the persisted top-300 day
inventory and writes an execution plan that tells the next runtime pass where to
spend the smallest number of odds requests:

1. prioritize matches that already have 2+ context confirmations;
2. fetch odds-api.io `/odds/multi` for matches with an odds_api_io event id;
3. use Bzzoiro/SStats/SportLogic only as secondary line/context probes when they
   already have a matched source id;
4. keep matches without source ids in a separate matching-needed bucket.

The plan is intentionally auditable.  It also writes per-match `price_backfill`
metadata into the day inventory so later runners/reports can see why a match is
still blocked from publication.
"""

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

UTC = timezone.utc
ROOT = Path('.').resolve()
DAY_INV_DIR = ROOT / '.data' / 'day_inventory'
EXPORT_DIR = ROOT / '.data' / 'exports'
OUT_JSON = EXPORT_DIR / 'latest-day-inventory-price-backfill-plan.json'
OUT_TXT = EXPORT_DIR / 'latest-day-inventory-price-backfill-plan.txt'
SUMMARY = EXPORT_DIR / 'latest-day-inventory-summary.json'


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return default


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')


def app_tz() -> ZoneInfo:
    try:
        return ZoneInfo(os.getenv('APP_TIMEZONE') or os.getenv('TZ') or 'Europe/Moscow')
    except Exception:
        return ZoneInfo('Europe/Moscow')


def target_date(now: datetime) -> str:
    explicit = str(os.getenv('DAY_INVENTORY_TARGET_DATE') or '').strip()
    return explicit or now.astimezone(app_tz()).date().isoformat()


def as_int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ''):
            return default
        return int(float(value))
    except Exception:
        return default


def parse_dt(value: Any) -> datetime | None:
    if value in (None, ''):
        return None
    try:
        text = str(value).strip()
        if text.endswith('Z'):
            text = text[:-1] + '+00:00'
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)
    except Exception:
        return None


def norm(value: Any) -> str:
    text = re.sub(r'[^a-z0-9]+', '_', str(value or '').strip().lower()).strip('_')
    aliases = {
        'oddsapiio': 'odds_api_io',
        'odds_api': 'odds_api_io',
        'bzzoiro_predictions': 'bzzoiro',
        'bzzoiro_current_odds': 'bzzoiro',
        'sstats_form': 'sstats',
        'football_data_org': 'football_data',
        'sportsdb': 'thesportsdb',
        'the_sports_db': 'thesportsdb',
    }
    return aliases.get(text, text)


def source_ids(row: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    raw = row.get('source_ids') if isinstance(row.get('source_ids'), dict) else {}
    for key, value in raw.items():
        src = norm(key)
        val = str(value or '').strip()
        if src and val:
            out[src] = val
    metadata = row.get('metadata') if isinstance(row.get('metadata'), dict) else {}
    for src in ('odds_api_io', 'bzzoiro', 'sstats', 'sportlogic'):
        for key in (f'{src}_event_id', f'{src}_id', f'{src}_match_id'):
            val = str(metadata.get(key) or '').strip()
            if val and src not in out:
                out[src] = val
    return out


def fixture_sources(row: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for key in ('fixture_sources', 'sources_seen'):
        value = row.get(key)
        if isinstance(value, list):
            out.extend(norm(x) for x in value if norm(x))
    out.extend(source_ids(row).keys())
    source = norm(row.get('source'))
    if source:
        out.append(source)
    seen = set()
    final = []
    for src in out:
        if src and src not in seen:
            seen.add(src)
            final.append(src)
    return final


def price_count(row: dict[str, Any]) -> int:
    metadata = row.get('metadata') if isinstance(row.get('metadata'), dict) else {}
    return max(
        as_int(metadata.get('price_confirmation_sources_count')),
        as_int(metadata.get('price_sources_count')),
        len(row.get('price_confirmations') or []),
        len(row.get('books') or []),
    )


def context_count(row: dict[str, Any]) -> int:
    metadata = row.get('metadata') if isinstance(row.get('metadata'), dict) else {}
    return max(
        as_int(metadata.get('context_sources_count')),
        as_int(metadata.get('confirmation_sources_count')),
        len(row.get('context_confirmations') or []),
        len(row.get('context_sources') or []),
    )


def route_for(row: dict[str, Any]) -> list[str]:
    ids = source_ids(row)
    routes: list[str] = []
    if ids.get('odds_api_io'):
        routes.append('odds_api_io:odds_multi')
    if ids.get('bzzoiro') or 'bzzoiro' in fixture_sources(row):
        routes.append('bzzoiro:current_odds_or_prediction')
    if ids.get('sstats') or 'sstats' in fixture_sources(row):
        routes.append('sstats:odds_snapshot_if_present')
    if ids.get('sportlogic'):
        routes.append('sportlogic:odds_detail_if_not_stale')
    if not routes:
        routes.append('needs_provider_match_first')
    return routes


def priority_tuple(row: dict[str, Any], now: datetime, min_price: int, min_context: int) -> tuple[int, float, int, str, str]:
    kickoff = parse_dt(row.get('kickoff_utc') or row.get('commence_time') or row.get('kickoff_local'))
    hours = 9999.0 if kickoff is None else (kickoff - now).total_seconds() / 3600.0
    if hours < -2:
        bucket = 8
    elif hours <= 6:
        bucket = 0
    elif hours <= 12:
        bucket = 1
    elif hours <= 24:
        bucket = 2
    else:
        bucket = 3
    # Context-ready matches should receive price requests first because one odds
    # fetch can make them publishable immediately.
    context_bonus = 0 if context_count(row) >= min_context else 1
    need = max(0, min_price - price_count(row))
    return (bucket, abs(hours), context_bonus + need, str(row.get('league_name') or ''), str(row.get('home_team') or ''))


def render(report: dict[str, Any]) -> str:
    lines = [
        '💸 Day inventory price backfill plan',
        f"• date_local: {report.get('date_local')}",
        f"• matches_total: {report.get('matches_total')}",
        f"• missing 2+ price: {report.get('missing_2plus_price')}",
        f"• context-ready but price-thin: {report.get('context_ready_price_thin')}",
        f"• odds_api_io event ids planned: {len(report.get('odds_api_io_event_ids', []))}",
        f"• bzzoiro secondary targets: {len(report.get('bzzoiro_targets', []))}",
        f"• sstats snapshot targets: {len(report.get('sstats_targets', []))}",
        '',
        'Top targets:',
    ]
    for item in (report.get('targets') or [])[:12]:
        lines.append(
            f"- {item.get('home_team')} — {item.get('away_team')} | price={item.get('price_confirmations')} "
            f"context={item.get('context_confirmations')} | route={','.join(item.get('routes') or [])}"
        )
    return '\n'.join(lines) + '\n'


def main() -> int:
    now = datetime.now(UTC)
    d = target_date(now)
    min_price = max(2, as_int(os.getenv('PUBLISH_MIN_ODDS_SOURCES') or os.getenv('CONTROLLED_FALLBACK_MIN_ODDS_SOURCES'), 2))
    min_context = max(2, as_int(os.getenv('PUBLISH_MIN_CONTEXT_SOURCES') or os.getenv('MIN_CONTEXT_SOURCES_PUBLISH'), 2))
    target_limit = max(1, as_int(os.getenv('PRICE_BACKFILL_TARGET_LIMIT'), 120))
    odds_id_limit = max(1, as_int(os.getenv('PRICE_BACKFILL_ODDS_API_IO_EVENT_LIMIT'), 60))
    bzz_limit = max(0, as_int(os.getenv('PRICE_BACKFILL_BZZOIRO_TARGET_LIMIT'), 40))
    sstats_limit = max(0, as_int(os.getenv('PRICE_BACKFILL_SSTATS_TARGET_LIMIT'), 60))

    inv_path = DAY_INV_DIR / f'{d}.json'
    inv = load_json(inv_path, {})
    matches = [dict(row) for row in inv.get('matches', []) if isinstance(row, dict)] if isinstance(inv, dict) else []
    targets: list[dict[str, Any]] = []
    missing_price = 0
    context_ready_price_thin = 0
    for row in sorted(matches, key=lambda r: priority_tuple(r, now, min_price, min_context)):
        pc = price_count(row)
        cc = context_count(row)
        if pc >= min_price:
            continue
        missing_price += 1
        if cc >= min_context:
            context_ready_price_thin += 1
        ids = source_ids(row)
        routes = route_for(row)
        item = {
            'match_key': row.get('match_key') or row.get('canonical_match_id'),
            'kickoff_utc': row.get('kickoff_utc') or row.get('commence_time'),
            'league_name': row.get('league_name'),
            'home_team': row.get('home_team'),
            'away_team': row.get('away_team'),
            'price_confirmations': pc,
            'context_confirmations': cc,
            'need_price_confirmations': max(0, min_price - pc),
            'fixture_sources': fixture_sources(row),
            'source_ids': ids,
            'routes': routes,
        }
        if len(targets) < target_limit:
            targets.append(item)
        row['price_backfill'] = {
            'updated_at_utc': now.isoformat(),
            'needed': True,
            'price_confirmations': pc,
            'context_confirmations': cc,
            'need_price_confirmations': max(0, min_price - pc),
            'routes': routes,
            'source_ids': ids,
        }
    odds_ids: list[str] = []
    bzz_targets: list[dict[str, Any]] = []
    sstats_targets: list[dict[str, Any]] = []
    match_first: list[dict[str, Any]] = []
    for item in targets:
        ids = item.get('source_ids') or {}
        if ids.get('odds_api_io') and len(odds_ids) < odds_id_limit:
            odds_ids.append(str(ids['odds_api_io']))
        if ('bzzoiro:current_odds_or_prediction' in (item.get('routes') or [])) and len(bzz_targets) < bzz_limit:
            bzz_targets.append(item)
        if ('sstats:odds_snapshot_if_present' in (item.get('routes') or [])) and len(sstats_targets) < sstats_limit:
            sstats_targets.append(item)
        if item.get('routes') == ['needs_provider_match_first'] and len(match_first) < 40:
            match_first.append(item)

    if isinstance(inv, dict):
        for row in inv.get('matches', []):
            if not isinstance(row, dict):
                continue
            pc = price_count(row)
            if pc >= min_price:
                row.pop('price_backfill', None)
        inv['updated_at_utc'] = now.isoformat()
        src_meta = inv.setdefault('sources', {})
        if isinstance(src_meta, dict):
            src_meta['price_backfill_plan'] = {
                'updated_at_utc': now.isoformat(),
                'targets': len(targets),
                'missing_2plus_price': missing_price,
                'context_ready_price_thin': context_ready_price_thin,
                'odds_api_io_event_ids': len(odds_ids),
                'bzzoiro_targets': len(bzz_targets),
                'sstats_targets': len(sstats_targets),
            }
        for path in [inv_path, DAY_INV_DIR / 'latest.json', DAY_INV_DIR / 'current.json', DAY_INV_DIR / 'today.json']:
            write_json(path, inv)
        summary = load_json(SUMMARY, {})
        if isinstance(summary, dict):
            summary['sources'] = dict(inv.get('sources') or {})
            summary['counts'] = dict(inv.get('counts') or summary.get('counts') or {})
            summary['updated_at_utc'] = now.isoformat()
            write_json(SUMMARY, summary)

    report = {
        'status': 'ok',
        'date_local': d,
        'updated_at_utc': now.isoformat(),
        'inventory_path': str(inv_path),
        'matches_total': len(matches),
        'min_price_confirmations': min_price,
        'min_context_sources': min_context,
        'missing_2plus_price': missing_price,
        'context_ready_price_thin': context_ready_price_thin,
        'target_limit': target_limit,
        'targets': targets,
        'odds_api_io_event_ids': odds_ids,
        'odds_api_io_event_ids_csv': ','.join(odds_ids),
        'bzzoiro_targets': bzz_targets,
        'sstats_targets': sstats_targets,
        'needs_provider_match_first': match_first,
        'notes': [
            'No external API calls are made by this planner.',
            'The next odds pass should spend requests on context-ready price-thin matches first.',
            'odds_api_io_event_ids_csv can be sent to /odds/multi in compact batches.',
            'Bzzoiro/SStats targets are secondary line probes only when source ids or fixture provenance already exist.',
        ],
    }
    write_json(OUT_JSON, report)
    OUT_TXT.write_text(render(report), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
