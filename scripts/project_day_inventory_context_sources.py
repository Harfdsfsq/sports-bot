from __future__ import annotations

"""Project explicit context-source evidence into day inventory and plan price backfill.

This script is intentionally cheap: it does not call external APIs.  It converts
stored fixture/provider provenance into auditable context fields, recomputes the
top-300 coverage counters, and then chains the no-API price-backfill planner so
provider-smoke artifacts always show the next minimal odds requests to spend.
"""

import json
import os
import re
import runpy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

UTC = timezone.utc
ROOT = Path('.').resolve()
DAY_INV_DIR = ROOT / '.data' / 'day_inventory'
EXPORT_DIR = ROOT / '.data' / 'exports'
OUT = EXPORT_DIR / 'latest-day-inventory-context-source-projection.json'
SUMMARY = EXPORT_DIR / 'latest-day-inventory-summary.json'

STRONG_CONTEXT = {'sstats', 'bzzoiro', 'api_football', 'futrixmetrics', 'sportlogic', 'clubelo'}
WEAK_CONTEXT = {'football_data', 'thesportsdb', 'allsportsapi', 'openfootball', 'openligadb', 'espn'}
NO_CONTEXT = {'', 'odds_api_io', 'line_history', 'market', 'ensemble'}
LIVE_ODDS_SOURCES = {'odds_api_io', 'bzzoiro', 'sportlogic'}


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


def norm(value: Any) -> str:
    text = re.sub(r'[^a-z0-9]+', '_', str(value or '').strip().lower()).strip('_')
    aliases = {
        'oddsapiio': 'odds_api_io',
        'odds_api': 'odds_api_io',
        'bzzoiro_predictions': 'bzzoiro',
        'bzzoiro_current_odds': 'bzzoiro',
        'sstats_form': 'sstats',
        'football_data_org': 'football_data',
        'the_sports_db': 'thesportsdb',
        'sportsdb': 'thesportsdb',
    }
    return aliases.get(text, text)


def list_from_any(value: Any) -> list[str]:
    if isinstance(value, dict):
        return [str(k).strip() for k in value.keys() if str(k).strip()]
    if isinstance(value, (list, tuple, set)):
        return [str(x).strip() for x in value if str(x).strip()]
    if isinstance(value, str) and value.strip():
        return [v.strip() for v in re.split(r'[,|;/]+', value) if v.strip()]
    return []


def unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        item = norm(value)
        if not item or item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def metadata(row: dict[str, Any]) -> dict[str, Any]:
    value = row.get('metadata')
    if isinstance(value, dict):
        return value
    value = {}
    row['metadata'] = value
    return value


def coverage(row: dict[str, Any]) -> dict[str, Any]:
    value = row.get('coverage')
    if isinstance(value, dict):
        return value
    value = {}
    row['coverage'] = value
    return value


def row_provider_sources(row: dict[str, Any]) -> list[str]:
    out: list[str] = []
    md = metadata(row)
    for key in ('sources_seen', 'fixture_sources', 'provider_sources', 'context_sources', 'context_confirmations'):
        out.extend(list_from_any(row.get(key)))
    for key in ('context_sources', 'context_confirmations', 'coverage_repair_sources', 'fixture_expansion_source', 'latest_run_source', 'source', 'provider'):
        out.extend(list_from_any(md.get(key)))
    source_ids = row.get('source_ids') if isinstance(row.get('source_ids'), dict) else {}
    out.extend(str(k) for k in source_ids.keys())
    out.append(str(row.get('source') or ''))
    return unique(out)


def price_count(row: dict[str, Any]) -> int:
    md = metadata(row)
    return max(
        as_int(md.get('price_confirmation_sources_count')),
        as_int(md.get('price_sources_count')),
        as_int(md.get('books_count')),
        len(row.get('price_confirmations') or []),
        len(row.get('books') or []),
    )


def odds_source_count(row: dict[str, Any]) -> int:
    return len({norm(x) for x in list_from_any(row.get('odds_sources')) + list_from_any(row.get('line_sources')) if norm(x) in LIVE_ODDS_SOURCES})


def existing_context(row: dict[str, Any]) -> set[str]:
    md = metadata(row)
    out = set(unique(list_from_any(row.get('context_sources')) + list_from_any(row.get('context_confirmations')) + list_from_any(md.get('context_sources')) + list_from_any(md.get('context_confirmations'))))
    return {x for x in out if x not in NO_CONTEXT}


def project_sources(row: dict[str, Any]) -> tuple[list[str], list[str], list[str]]:
    cov = coverage(row)
    md = metadata(row)
    provenance = set(row_provider_sources(row))
    existing = existing_context(row)
    strong = {x for x in provenance if x in STRONG_CONTEXT}
    weak = {x for x in provenance if x in WEAK_CONTEXT}
    reasons: list[str] = []
    sources = set(existing)
    if existing:
        reasons.append('existing_context_source_fields')
    if strong:
        sources |= strong
        reasons.append('provider_payload_is_context_capable')
    if (cov.get('context') or cov.get('xg') or cov.get('form')) and weak:
        sources |= weak
        reasons.append('coverage_context_with_fixture_provider')
    sources -= NO_CONTEXT
    confirmations = set(sources)
    return sorted(sources), sorted(confirmations), sorted(set(reasons))


def recompute_counts(matches: list[dict[str, Any]], previous: dict[str, Any], min_price: int, min_context: int) -> dict[str, Any]:
    counts = dict(previous or {})
    totals = {
        'matches_total': len(matches),
        'matches_with_odds': 0,
        'matches_with_context': 0,
        'matches_with_2plus_price_confirmations': 0,
        'matches_with_2plus_odds_sources': 0,
        'matches_with_2plus_context_sources': 0,
        'matches_ready_for_model': 0,
        'matches_ready_for_publish': 0,
        'matches_missing_price_2plus': 0,
        'matches_missing_context_2plus': 0,
    }
    for row in matches:
        cov = coverage(row)
        md = metadata(row)
        pc = price_count(row)
        oc = odds_source_count(row)
        cc = max(len(row.get('context_confirmations') or []), len(row.get('context_sources') or []))
        has_odds = bool(cov.get('odds')) or pc > 0
        has_context = bool(cov.get('context')) or cc > 0
        ok_price = pc >= min_price
        ok_odds_sources = oc >= min_price
        ok_context = cc >= min_context
        totals['matches_with_odds'] += int(has_odds)
        totals['matches_with_context'] += int(has_context)
        totals['matches_with_2plus_price_confirmations'] += int(ok_price)
        totals['matches_with_2plus_odds_sources'] += int(ok_odds_sources)
        totals['matches_with_2plus_context_sources'] += int(ok_context)
        totals['matches_ready_for_model'] += int(bool(cov.get('ready_for_model')) or (has_odds and has_context))
        totals['matches_ready_for_publish'] += int(ok_price and ok_odds_sources and ok_context and has_odds and has_context)
        totals['matches_missing_price_2plus'] += int(not ok_price)
        totals['matches_missing_context_2plus'] += int(not ok_context)
    counts.update(totals)
    counts['publish_min_price_confirmations'] = min_price
    counts['publish_min_odds_sources'] = min_price
    counts['publish_min_context_sources'] = min_context
    counts['context_source_projection_updated_utc'] = datetime.now(UTC).isoformat()
    return counts


def run_price_backfill_planner() -> dict[str, Any]:
    path = ROOT / 'scripts' / 'plan_day_inventory_price_backfill.py'
    started = datetime.now(UTC).isoformat()
    if not path.exists():
        return {'status': 'skipped', 'reason': 'missing_script', 'path': str(path), 'started_at_utc': started}
    try:
        runpy.run_path(str(path), run_name='__main__')
        return {'status': 'ok', 'path': str(path), 'started_at_utc': started, 'finished_at_utc': datetime.now(UTC).isoformat()}
    except SystemExit as exc:
        code = getattr(exc, 'code', 0)
        if code in (0, None):
            return {'status': 'ok', 'path': str(path), 'started_at_utc': started, 'finished_at_utc': datetime.now(UTC).isoformat(), 'code': code}
        return {'status': 'error', 'path': str(path), 'started_at_utc': started, 'finished_at_utc': datetime.now(UTC).isoformat(), 'code': code}
    except Exception as exc:
        return {'status': 'error', 'path': str(path), 'started_at_utc': started, 'finished_at_utc': datetime.now(UTC).isoformat(), 'error': f'{type(exc).__name__}: {exc}'}


def main() -> int:
    now = datetime.now(UTC)
    now_iso = now.isoformat()
    d = target_date(now)
    min_price = max(2, as_int(os.getenv('PUBLISH_MIN_ODDS_SOURCES') or os.getenv('CONTROLLED_FALLBACK_MIN_ODDS_SOURCES'), 2))
    min_context = max(2, as_int(os.getenv('PUBLISH_MIN_CONTEXT_SOURCES') or os.getenv('MIN_CONTEXT_SOURCES_PUBLISH'), 2))
    inv_path = DAY_INV_DIR / f'{d}.json'
    inv = load_json(inv_path, {})
    if not isinstance(inv, dict):
        inv = {'date_local': d, 'matches': []}
    matches = [dict(row) for row in inv.get('matches', []) if isinstance(row, dict)]
    changed = projected = ready_publish_after = 0
    examples: list[dict[str, Any]] = []
    for row in matches:
        before = json.dumps(row, ensure_ascii=False, sort_keys=True)
        cov = coverage(row)
        md = metadata(row)
        sources, confirmations, reasons = project_sources(row)
        if sources or confirmations:
            projected += 1
        row['context_sources'] = sources
        row['context_confirmations'] = confirmations
        md['context_sources_count'] = max(as_int(md.get('context_sources_count')), len(sources))
        md['confirmation_sources_count'] = max(as_int(md.get('confirmation_sources_count')), len(confirmations))
        md['context_source_projection_updated_utc'] = now_iso
        if reasons:
            md['context_source_projection_reasons'] = reasons
        pc = price_count(row)
        oc = odds_source_count(row)
        cc = max(len(confirmations), len(sources))
        has_odds = bool(cov.get('odds')) or pc > 0
        has_context = bool(cov.get('context')) or cc > 0
        cov['context'] = has_context
        cov['context_2plus_sources'] = cc >= min_context
        cov['odds_2plus_sources'] = oc >= min_price
        cov['ready_for_model'] = bool(cov.get('ready_for_model')) or (has_odds and has_context)
        cov['ready_for_publish'] = pc >= min_price and oc >= min_price and cc >= min_context and has_odds and has_context
        row['coverage_gaps'] = {
            'price_confirmations': pc,
            'odds_sources': oc,
            'context_confirmations': cc,
            'need_price_confirmations': max(0, min_price - pc),
            'need_odds_sources': max(0, min_price - oc),
            'need_context_confirmations': max(0, min_context - cc),
            'has_odds': has_odds,
            'has_context': has_context,
        }
        if cov['ready_for_publish']:
            ready_publish_after += 1
        changed += int(before != json.dumps(row, ensure_ascii=False, sort_keys=True))
        if len(examples) < 12 and (pc < min_price or cc < min_context):
            examples.append({
                'match_key': row.get('match_key') or row.get('canonical_match_id'),
                'home_team': row.get('home_team'),
                'away_team': row.get('away_team'),
                'fixture_sources': row.get('fixture_sources') or row.get('sources_seen'),
                'price_confirmations': pc,
                'context_confirmations': cc,
                'gaps': row['coverage_gaps'],
            })
    inv['matches'] = matches
    inv['counts'] = recompute_counts(matches, inv.get('counts') if isinstance(inv.get('counts'), dict) else {}, min_price, min_context)
    inv['updated_at_utc'] = now_iso
    sources_meta = inv.setdefault('sources', {})
    if isinstance(sources_meta, dict):
        sources_meta['context_source_projection'] = {
            'updated_at_utc': now_iso,
            'rows_seen': len(matches),
            'rows_changed': changed,
            'rows_projected': projected,
            'ready_for_publish_after': ready_publish_after,
            'min_price_confirmations': min_price,
            'min_context_sources': min_context,
        }
    for path in [inv_path, DAY_INV_DIR / 'latest.json', DAY_INV_DIR / 'current.json', DAY_INV_DIR / 'today.json']:
        write_json(path, inv)
    summary = {
        'date_local': d,
        'updated_at_utc': now_iso,
        'timezone': str(app_tz()),
        'build_status': inv.get('build_status') or 'ok',
        'counts': inv.get('counts', {}),
        'source_match_counts': dict(inv.get('source_match_counts') or {}),
        'league_match_counts': dict(inv.get('league_match_counts') or {}),
        'sources': dict(inv.get('sources') or {}),
    }
    write_json(SUMMARY, summary)
    report = {
        'status': 'ok',
        'date_local': d,
        'updated_at_utc': now_iso,
        'inventory_path': str(inv_path),
        'rows_seen': len(matches),
        'rows_changed': changed,
        'rows_projected': projected,
        'ready_for_publish_after': ready_publish_after,
        'counts': inv.get('counts', {}),
        'gap_examples': examples,
        'price_backfill_plan': None,
        'notes': [
            'Projects explicit context_sources/context_confirmations from provider provenance and existing xg/form/context flags.',
            'Does not invent odds prices; price confirmations still come from odds/bookmaker evidence.',
            'Chains the no-API price-backfill planner so provider-smoke exposes the next minimal odds requests.',
        ],
    }
    write_json(OUT, report)
    planner_status = run_price_backfill_planner()
    report['price_backfill_plan'] = planner_status
    write_json(OUT, report)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
