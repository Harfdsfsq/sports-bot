from __future__ import annotations

import asyncio
import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.config import Settings
from app.providers.bzzoiro_v2 import BzzoiroContextProvider
from app.schemas import Match
from app.utils import canonicalize_league_name, canonicalize_team_name, parse_datetime

ROOT = Path('.').resolve()
DAY_DIR = ROOT / '.data' / 'day_inventory'
EXPORT_DIR = ROOT / '.data' / 'exports'
OUT = EXPORT_DIR / 'latest-bzzoiro-v2-inventory-target-enrichment.json'


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


def truthy(value: Any, default: bool = True) -> bool:
    if value in (None, ''):
        return default
    return str(value).strip().lower() in {'1', 'true', 'yes', 'on', 'force'}


def as_int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ''):
            return default
        return int(float(str(value)))
    except Exception:
        return default


def items(value: Any) -> list[str]:
    if isinstance(value, dict):
        return [str(k).strip() for k in value if str(k).strip()]
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    if isinstance(value, str) and value.strip():
        return [x.strip() for x in re.split(r'[,|;/]+', value) if x.strip()]
    return []


def add_unique(row: dict[str, Any], field: str, value: str) -> None:
    current = items(row.get(field))
    seen = {x.lower() for x in current}
    if value.lower() not in seen:
        current.append(value)
    row[field] = current


def parse_dt(row: dict[str, Any]) -> datetime | None:
    for key in ('kickoff_utc', 'commence_time', 'start_time', 'kickoff'):
        raw = row.get(key)
        if raw in (None, ''):
            continue
        try:
            dt = parse_datetime(raw)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            return dt.astimezone(UTC)
        except Exception:
            continue
    return None


def row_needs_bzzoiro(row: dict[str, Any]) -> bool:
    odds_sources = {x.lower() for x in items(row.get('odds_sources')) + items(row.get('line_sources'))}
    context_sources = {x.lower() for x in items(row.get('context_sources')) + items(row.get('context_confirmations'))}
    need_context = len(context_sources) < 2 and 'bzzoiro' not in context_sources and 'bzzoiro_v2' not in context_sources
    need_odds = len(odds_sources & {'odds_api_io', 'bzzoiro', 'sportlogic'}) < 2 and 'bzzoiro' not in odds_sources and 'bzzoiro_v2' not in odds_sources
    return need_context or need_odds


def make_match(row: dict[str, Any], idx: int) -> Match | None:
    home = str(row.get('home_team') or row.get('home') or '').strip()
    away = str(row.get('away_team') or row.get('away') or '').strip()
    dt = parse_dt(row)
    if not home or not away or dt is None:
        return None
    return Match(
        source='day_inventory',
        source_event_id=str(row.get('source_event_id') or row.get('match_key') or row.get('canonical_match_id') or idx),
        sport_key='soccer',
        league_name=str(row.get('league_name') or row.get('league') or ''),
        home_team=home,
        away_team=away,
        commence_time=dt,
        home_team_norm=canonicalize_team_name(home),
        away_team_norm=canonicalize_team_name(away),
        league_key=canonicalize_league_name(str(row.get('league_name') or row.get('league') or '')),
        metadata={'inventory_index': idx, 'inventory_match_key': row.get('match_key') or row.get('canonical_match_id')},
    )


def aliases_for_payload(day: str) -> list[Path]:
    return [DAY_DIR / f'{day}.json', DAY_DIR / 'current.json', DAY_DIR / 'latest.json', DAY_DIR / 'today.json']


def load_inventory(day: str) -> dict[str, Any]:
    return load(DAY_DIR / f'{day}.json', {}) or load(DAY_DIR / 'latest.json', {})


async def run_pool_id_prefill() -> dict[str, Any]:
    if not truthy(os.getenv('BZZOIRO_POOL_ID_INVENTORY_ENRICHMENT_ENABLED'), True):
        return {'status': 'disabled'}
    try:
        from scripts.enrich_inventory_bzzoiro_pool_ids import run as pool_run

        result = await pool_run()
        return result if isinstance(result, dict) else {'status': 'ok', 'result_type': type(result).__name__}
    except Exception as exc:
        return {'status': 'error_ignored', 'error': f'{type(exc).__name__}: {exc}'}


async def run() -> dict[str, Any]:
    if not truthy(os.getenv('BZZOIRO_V2_INVENTORY_TARGET_ENRICHMENT_ENABLED'), True):
        return {'status': 'disabled'}
    if (
        os.getenv('RUNBOT_DISCOVERY_FIRST_PREPARE_RUNNING') == '1'
        and not truthy(os.getenv('RUNBOT_FULL_BZZOIRO_GAP_ENRICHMENT_ENABLED'), False)
    ):
        report = {
            'status': 'deferred_to_prediction_runner',
            'created_at_utc': datetime.now(UTC).isoformat(),
            'reason': 'focused_alpha_bounded_provider_refresh',
            'publication_contract_relaxed': False,
        }
        write(OUT, report)
        return report
    day = str(os.getenv('DAY_INVENTORY_TARGET_DATE') or os.getenv('DAY_INVENTORY_CACHE_DATE') or datetime.now(UTC).date().isoformat())[:10]
    pool_prefill = await run_pool_id_prefill()
    payload = load_inventory(day)
    rows = payload.get('matches') if isinstance(payload.get('matches'), list) else []
    if not rows:
        return {'status': 'no_inventory', 'pool_id_prefill': pool_prefill}
    limit = max(1, as_int(os.getenv('BZZOIRO_V2_INVENTORY_TARGET_LIMIT'), 220))
    match_map: dict[str, int] = {}
    targets: list[Match] = []
    for idx, row in enumerate(rows):
        if not isinstance(row, dict) or not row_needs_bzzoiro(row):
            continue
        match = make_match(row, idx)
        if match is None:
            continue
        if match.match_key in match_map:
            continue
        match_map[match.match_key] = idx
        targets.append(match)
        if len(targets) >= limit:
            break
    if not targets:
        return {'status': 'no_targets', 'inventory_rows': len(rows), 'pool_id_prefill': pool_prefill}
    provider = BzzoiroContextProvider(Settings())
    contexts, stats, preview = await provider.fetch_context(targets)
    contexts = dict(contexts or {})
    touched = 0
    examples: list[dict[str, Any]] = []
    for match_key, ctx in contexts.items():
        idx = match_map.get(str(match_key))
        if idx is None or not isinstance(rows[idx], dict):
            continue
        row = rows[idx]
        before = json.dumps(row, ensure_ascii=False, sort_keys=True, default=str)
        add_unique(row, 'context_sources', 'bzzoiro')
        add_unique(row, 'context_confirmations', 'bzzoiro')
        row['context_sources_count'] = max(as_int(row.get('context_sources_count')), len(items(row.get('context_sources'))))
        row['has_context'] = True
        if ctx.expected_home is not None:
            row['expected_home'] = ctx.expected_home
        if ctx.expected_away is not None:
            row['expected_away'] = ctx.expected_away
        source_summary = row.setdefault('source_summary', {}) if isinstance(row.setdefault('source_summary', {}), dict) else {}
        source_summary['bzzoiro_v2_inventory_target_enriched'] = True
        source_summary['bzzoiro_v2_context_confidence'] = getattr(ctx, 'confidence', None)
        source_summary['bzzoiro_v2_context_details'] = getattr(ctx, 'details', {}) or {}
        hints = (getattr(ctx, 'details', {}) or {}).get('provider_odds_hints')
        if isinstance(hints, list) and hints:
            add_unique(row, 'odds_sources', 'bzzoiro')
            add_unique(row, 'line_sources', 'bzzoiro')
            row['odds_sources_count'] = max(as_int(row.get('odds_sources_count')), len({x for x in items(row.get('odds_sources')) if x in {'odds_api_io', 'bzzoiro', 'sportlogic'}}))
            source_summary['bzzoiro_v2_provider_odds_hints_count'] = len(hints)
            source_summary['bzzoiro_v2_provider_odds_hints_sample'] = hints[:20]
        if json.dumps(row, ensure_ascii=False, sort_keys=True, default=str) != before:
            touched += 1
            if len(examples) < 12:
                examples.append(
                    {
                        'match_key': match_key,
                        'home': row.get('home_team'),
                        'away': row.get('away_team'),
                        'expected_home': row.get('expected_home'),
                        'expected_away': row.get('expected_away'),
                        'odds_sources': row.get('odds_sources'),
                        'context_sources': row.get('context_sources'),
                    }
                )
    payload['matches'] = rows
    payload['bzzoiro_v2_inventory_target_updated_at_utc'] = datetime.now(UTC).isoformat()
    for path in aliases_for_payload(day):
        write(path, payload)
    report = {
        'status': 'ok',
        'created_at_utc': datetime.now(UTC).isoformat(),
        'inventory_rows': len(rows),
        'target_limit': limit,
        'targets_selected': len(targets),
        'contexts_matched': len(contexts),
        'rows_touched': touched,
        'pool_id_prefill': pool_prefill,
        'stats': stats,
        'preview': preview,
        'examples': examples,
    }
    write(OUT, report)
    return report


def main() -> int:
    report = asyncio.run(run())
    write(OUT, report)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
