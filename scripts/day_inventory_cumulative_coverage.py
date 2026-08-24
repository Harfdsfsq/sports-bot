from __future__ import annotations

import json
import os
import re
import runpy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.services.publication_thresholds import publish_min_context_sources, publish_min_odds_sources
from zoneinfo import ZoneInfo

ROOT = Path('.').resolve()
UTC = timezone.utc
OUT = ROOT / '.data' / 'exports' / 'latest-day-inventory-cumulative-coverage.json'
LIVE_ODDS_SOURCES = {'odds_api_io', 'bzzoiro', 'sportlogic'}
CONFLICT_MARKERS = ('<' * 7, '=' * 7, '>' * 7)
ENRICHMENT_FIELDS = (
    'expected_home',
    'expected_away',
    'sstats_expected_home',
    'sstats_expected_away',
    'sstats_xg_source',
    'sstats_lambda_home',
    'sstats_lambda_away',
    'sstats_form_games',
    'sstats_offer_count',
    'sstats_offer_books',
    'sstats_offer_points',
    'sstats_game_id',
)
EMPTY_VALUES = (None, '', [], {})


def load_json(path: Path, default: Any) -> Any:
    try:
        text = path.read_text(encoding='utf-8', errors='replace')
        if any(marker in text for marker in CONFLICT_MARKERS):
            return default
        return json.loads(text)
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


def target_date() -> str:
    explicit = str(os.getenv('DAY_INVENTORY_TARGET_DATE') or '').strip()
    return explicit or datetime.now(UTC).astimezone(app_tz()).date().isoformat()


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


def as_int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ''):
            return default
        return int(float(value))
    except Exception:
        return default


def as_float(value: Any) -> float | None:
    try:
        if value in (None, ''):
            return None
        return float(value)
    except Exception:
        return None


def source_count(row: dict[str, Any], *names: str) -> int:
    best = 0
    containers = [row]
    for key in ('metadata', 'source_summary', 'market_summary', 'price_summary', 'integrity_report'):
        val = row.get(key)
        if isinstance(val, dict):
            containers.append(val)
    for c in containers:
        for name in names:
            best = max(best, as_int(c.get(name)))
    return best


def list_from_any(value: Any) -> list[str]:
    if isinstance(value, dict):
        return [str(k).strip() for k in value.keys() if str(k).strip()]
    if isinstance(value, (list, tuple, set)):
        return [str(x).strip() for x in value if str(x).strip()]
    if isinstance(value, str) and value.strip():
        return [v.strip() for v in re.split(r'[,|;/]+', value) if v.strip()]
    return []


def norm_source(value: Any) -> str:
    text = re.sub(r'[^a-z0-9]+', '_', str(value or '').strip().lower()).strip('_')
    aliases = {
        'oddsapiio': 'odds_api_io',
        'odds_api': 'odds_api_io',
        'odds_api_io_account1': 'odds_api_io',
        'odds_api_io_account2': 'odds_api_io',
        'bzzoiro_predictions': 'bzzoiro',
        'bzzoiro_current_odds': 'bzzoiro',
        'bzzoiro_v2': 'bzzoiro',
        'sport_logic': 'sportlogic',
        'sportlogic_io': 'sportlogic',
    }
    return aliases.get(text, text)


def norm_text(value: Any) -> str:
    return re.sub(r'[^a-z0-9]+', ' ', str(value or '').strip().lower()).strip()


def odds_source_count(row: dict[str, Any]) -> int:
    return len({norm_source(x) for x in list_from_any(row.get('odds_sources')) + list_from_any(row.get('line_sources')) if norm_source(x) in LIVE_ODDS_SOURCES})


def price_confirmation_count(row: dict[str, Any]) -> int:
    return source_count(row, 'price_confirmation_sources_count', 'price_sources_count', 'books_count', 'latest_books_max')


def context_source_count(row: dict[str, Any]) -> int:
    values = []
    for source in list_from_any(row.get('context_sources')) + list_from_any(row.get('context_confirmations')):
        item = norm_source(source)
        if item.startswith('provider_'):
            item = item.removeprefix('provider_')
        if item in {'', 'ensemble', 'market', 'market_signal', 'line_history', 'odds_api_io', 'xg_model_context', 'form_context'}:
            continue
        if re.match(r'^context_(source|confirmation)_\d+$', item):
            continue
        values.append(item)
    return len(set(values))


def bool_cov(row: dict[str, Any], key: str) -> bool:
    cov = row.get('coverage') if isinstance(row.get('coverage'), dict) else {}
    return bool(cov.get(key))


def rows_from_inventory(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    rows = payload.get('matches') if isinstance(payload.get('matches'), list) else []
    return [row for row in rows if isinstance(row, dict)]


def inventory_date(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ''
    for key in ('date_local', 'target_date', 'date', 'inventory_date'):
        value = str(payload.get(key) or '').strip()
        if value:
            return value[:10]
    return ''


def has_real_xg(row: dict[str, Any]) -> bool:
    source = norm_source(row.get('sstats_xg_source'))
    if not source or 'market' in source or 'proxy' in source:
        return False
    home = as_float(row.get('sstats_expected_home'))
    away = as_float(row.get('sstats_expected_away'))
    return home is not None and away is not None


def real_xg_rows(payload: Any) -> int:
    return sum(1 for row in rows_from_inventory(payload) if has_real_xg(row))


def row_identity_keys(row: dict[str, Any]) -> list[str]:
    keys: list[str] = []
    for name in ('match_key', 'canonical_match_id'):
        value = str(row.get(name) or '').strip().lower()
        if value:
            keys.append(value)
    game_id = str(row.get('sstats_game_id') or '').strip()
    if game_id:
        keys.append(f'sstats_game_id:{game_id}')
    home = norm_text(row.get('home_team'))
    away = norm_text(row.get('away_team'))
    if home and away:
        pair = ' | '.join(sorted((home, away)))
        kickoff = parse_dt(row.get('kickoff_utc') or row.get('commence_time') or row.get('kickoff_local'))
        if kickoff is not None:
            keys.append(f'pair:{pair}|{kickoff.date().isoformat()}')
        keys.append(f'pair:{pair}')
    return keys


def coverage_score(payload: dict[str, Any]) -> int:
    total = 0
    for row in rows_from_inventory(payload):
        total += int(bool_cov(row, 'odds') or price_confirmation_count(row) > 0)
        total += int(bool_cov(row, 'context') or context_source_count(row) > 0)
        total += odds_source_count(row)
        total += context_source_count(row)
    return total


def inventory_candidates(d: str) -> list[Path]:
    names = [f'{d}.json', 'latest.json', 'current.json', 'today.json']
    bases = [
        ROOT / '.data' / 'day_inventory',
        ROOT / '.data' / 'cache' / 'day_inventory',
        ROOT / 'artifacts' / 'run-bot',
    ]
    paths: list[Path] = []
    for base in bases:
        paths.extend(base / name for name in names)
    paths.extend([
        ROOT / 'artifacts' / 'run-bot' / 'day_inventory-latest.json',
        ROOT / 'artifacts' / 'run-bot' / 'day_inventory-current.json',
        ROOT / 'artifacts' / 'run-bot' / 'day_inventory-today.json',
    ])
    out: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        key = str(path)
        if key not in seen:
            seen.add(key)
            out.append(path)
    return out


def file_mtime(path: Path) -> float:
    try:
        return float(path.stat().st_mtime)
    except Exception:
        return 0.0


def best_inventory(d: str) -> tuple[Path, dict[str, Any], dict[str, Any]]:
    date_path = ROOT / '.data' / 'day_inventory' / f'{d}.json'
    best_path = date_path
    best_payload = load_json(date_path, {'matches': []})
    best_score = (-1, -1, -1, -1, -1.0, '')
    for path in inventory_candidates(d):
        payload = load_json(path, {})
        if not isinstance(payload, dict):
            continue
        rows = rows_from_inventory(payload)
        if not rows:
            continue
        pdate = inventory_date(payload)
        date_ok = 1 if not pdate or pdate == d else 0
        score = (date_ok, len(rows), coverage_score(payload), real_xg_rows(payload), file_mtime(path), str(path))
        if score > best_score:
            best_score = score
            best_path = path
            best_payload = payload
    report = {
        'selected_path': str(best_path),
        'selected_rows': len(rows_from_inventory(best_payload)),
        'selected_real_xg_rows': real_xg_rows(best_payload),
        'date_path': str(date_path),
        'date_path_rows': len(rows_from_inventory(load_json(date_path, {}))),
        'score': list(best_score),
    }
    return date_path, best_payload, report


def merge_enrichment(payload: dict[str, Any], d: str) -> dict[str, Any]:
    target_rows = rows_from_inventory(payload)
    before = sum(1 for row in target_rows if has_real_xg(row))
    donors: dict[str, dict[str, Any]] = {}
    donor_files: list[str] = []
    for path in inventory_candidates(d):
        candidate = load_json(path, {})
        rows = [row for row in rows_from_inventory(candidate) if has_real_xg(row)]
        if not rows:
            continue
        donor_files.append(str(path))
        for row in rows:
            for key in row_identity_keys(row):
                donors.setdefault(key, row)
    rows_filled = 0
    fields_written = 0
    for row in target_rows:
        if has_real_xg(row):
            continue
        donor: dict[str, Any] | None = None
        for key in row_identity_keys(row):
            donor = donors.get(key)
            if donor is not None:
                break
        if donor is None:
            continue
        wrote = False
        for field in ENRICHMENT_FIELDS:
            value = donor.get(field)
            if value in EMPTY_VALUES:
                continue
            if row.get(field) not in EMPTY_VALUES:
                continue
            row[field] = value
            fields_written += 1
            wrote = True
        if wrote:
            rows_filled += 1
    return {
        'donor_files': donor_files[:12],
        'donor_keys': len(donors),
        'rows_seen': len(target_rows),
        'rows_filled': rows_filled,
        'fields_written': fields_written,
        'real_xg_rows_before': before,
        'real_xg_rows_after': sum(1 for row in target_rows if has_real_xg(row)),
    }


def write_inventory_aliases(d: str, payload: dict[str, Any]) -> None:
    payload['updated_at_utc'] = datetime.now(UTC).isoformat()
    for path in [
        ROOT / '.data' / 'day_inventory' / f'{d}.json',
        ROOT / '.data' / 'day_inventory' / 'latest.json',
        ROOT / '.data' / 'day_inventory' / 'current.json',
        ROOT / '.data' / 'day_inventory' / 'today.json',
    ]:
        write_json(path, payload)


def bucket(minutes: float) -> str:
    if minutes < 0:
        return 'started'
    if minutes <= 120:
        return '0_2h'
    if minutes <= 240:
        return '2_4h'
    if minutes <= 360:
        return '4_6h'
    if minutes <= 720:
        return '6_12h'
    return '12h_plus'


def empty_bucket() -> dict[str, Any]:
    return {
        'seen': 0,
        'odds_any': 0,
        'odds_2plus_sources': 0,
        'context_any': 0,
        'context_2plus_sources': 0,
        'ready_for_model': 0,
        'ready_for_publish': 0,
    }


def run_python_script(path: Path) -> dict[str, Any]:
    started = datetime.now(UTC).isoformat()
    try:
        if not path.exists():
            return {'path': str(path), 'status': 'skipped', 'reason': 'missing', 'started_at_utc': started}
        runpy.run_path(str(path), run_name='__main__')
        return {'path': str(path), 'status': 'ok', 'started_at_utc': started, 'finished_at_utc': datetime.now(UTC).isoformat()}
    except SystemExit as exc:
        code = getattr(exc, 'code', 0)
        if code in (0, None):
            return {'path': str(path), 'status': 'ok', 'started_at_utc': started, 'finished_at_utc': datetime.now(UTC).isoformat(), 'code': code}
        return {'path': str(path), 'status': 'error', 'started_at_utc': started, 'finished_at_utc': datetime.now(UTC).isoformat(), 'code': code}
    except Exception as exc:
        return {
            'path': str(path),
            'status': 'error',
            'started_at_utc': started,
            'finished_at_utc': datetime.now(UTC).isoformat(),
            'error': f'{type(exc).__name__}: {exc}',
        }


def ensure_latest_run_coverage_merged() -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = []
    steps.append(run_python_script(ROOT / 'scripts' / 'match_data_coverage_report.py'))
    steps.append(run_python_script(ROOT / 'scripts' / 'merge_run_coverage_into_day_inventory.py'))
    steps.append(run_python_script(ROOT / 'scripts' / 'repair_inventory_source_counts.py'))
    steps.append(run_python_script(ROOT / 'scripts' / 'build_day_inventory_coverage_truth.py'))
    return steps


def preserve_enrichment_before_pipeline(d: str) -> dict[str, Any]:
    _, payload, selection = best_inventory(d)
    merge = merge_enrichment(payload, d)
    if merge['rows_filled']:
        write_inventory_aliases(d, payload)
    return {'inventory_selection': selection, 'merge': merge, 'aliases_written': bool(merge['rows_filled'])}


def main() -> int:
    d = target_date()
    pre_merge = preserve_enrichment_before_pipeline(d)
    pipeline_steps = ensure_latest_run_coverage_merged()
    now = datetime.now(UTC)
    inv_path, inv, inventory_selection = best_inventory(d)
    post_merge = merge_enrichment(inv, d)
    matches = rows_from_inventory(inv)
    previous = inv.get('coverage_progress') if isinstance(inv.get('coverage_progress'), dict) else {}
    prev_buckets = previous.get('by_kickoff_window') if isinstance(previous.get('by_kickoff_window'), dict) else {}
    min_odds_sources = publish_min_odds_sources()
    min_context_sources = publish_min_context_sources()
    current = {k: empty_bucket() for k in ('0_2h', '2_4h', '4_6h', '6_12h', '12h_plus', 'started')}
    samples_missing: list[dict[str, Any]] = []
    for row in matches:
        kickoff = parse_dt(row.get('kickoff_utc') or row.get('commence_time') or row.get('kickoff_local'))
        if kickoff is None:
            continue
        b = bucket((kickoff - now).total_seconds() / 60.0)
        slot = current.setdefault(b, empty_bucket())
        slot['seen'] += 1
        odds_sources = odds_source_count(row)
        price_confirmations = price_confirmation_count(row)
        context_sources = context_source_count(row)
        has_odds = bool_cov(row, 'odds') or price_confirmations > 0
        has_context = bool_cov(row, 'context') or context_sources > 0
        ready_model = bool_cov(row, 'ready_for_model') or (has_odds and has_context)
        ready_publish = price_confirmations >= min_odds_sources and odds_sources >= min_odds_sources and context_sources >= min_context_sources
        slot['odds_any'] += int(has_odds)
        slot['odds_2plus_sources'] += int(odds_sources >= min_odds_sources)
        slot['context_any'] += int(has_context)
        slot['context_2plus_sources'] += int(context_sources >= min_context_sources)
        slot['ready_for_model'] += int(ready_model)
        slot['ready_for_publish'] += int(ready_publish)
        if b != 'started' and len(samples_missing) < 15 and not (odds_sources >= min_odds_sources and context_sources >= min_context_sources):
            samples_missing.append({
                'match_key': row.get('match_key') or row.get('canonical_match_id'),
                'kickoff_utc': kickoff.isoformat(),
                'league_name': row.get('league_name'),
                'home_team': row.get('home_team'),
                'away_team': row.get('away_team'),
                'bucket': b,
                'odds_sources': odds_sources,
                'price_confirmations': price_confirmations,
                'context_sources': context_sources,
                'has_odds': has_odds,
                'has_context': has_context,
                'has_real_xg': has_real_xg(row),
            })
    high_watermark: dict[str, dict[str, Any]] = {}
    for name, data in current.items():
        old = prev_buckets.get(name) if isinstance(prev_buckets.get(name), dict) else {}
        high_watermark[name] = {k: max(as_int(old.get(k)), as_int(data.get(k))) for k in empty_bucket().keys()}
    pipeline_errors = [step for step in pipeline_steps if isinstance(step, dict) and step.get('status') == 'error']
    enrichment_merge = {
        'before_pipeline': pre_merge,
        'after_pipeline': post_merge,
        'real_xg_rows_final': real_xg_rows(inv),
    }
    progress = {
        'updated_at_utc': now.isoformat(),
        'date_local': d,
        'inventory_selection': inventory_selection,
        'enrichment_merge': enrichment_merge,
        'min_odds_sources': min_odds_sources,
        'min_context_sources': min_context_sources,
        'coverage_pipeline_steps': pipeline_steps,
        'coverage_pipeline_ok': not pipeline_errors,
        'coverage_pipeline_error_count': len(pipeline_errors),
        'current_by_kickoff_window': current,
        'by_kickoff_window': high_watermark,
        'notes': [
            'current_by_kickoff_window is the live rolling window and can shrink when matches start.',
            'by_kickoff_window is the cumulative high watermark and should only grow during the local day.',
            'This script selects the largest valid inventory alias before writing cumulative coverage, so a smaller date-file cannot shrink the day pool.',
            'Inventory selection also prefers real provider xG coverage and file freshness, so a stale copy cannot replace enriched rows.',
            'Enrichment fields are merged back onto the surviving rows by match key, canonical id, sstats game id or team pair.',
            'ready_for_publish requires 2+ price confirmations, 2+ independent live odds providers, and 2+ context/confirmation sources by default.',
        ],
    }
    inv['coverage_progress'] = progress
    if isinstance(inv.get('counts'), dict):
        inv['counts']['coverage_progress_updated_utc'] = now.isoformat()
        inv['counts']['publish_min_odds_sources'] = min_odds_sources
        inv['counts']['publish_min_context_sources'] = min_context_sources
        inv['counts']['matches_total'] = max(as_int(inv['counts'].get('matches_total')), len(matches))
        inv['counts']['real_xg_rows'] = real_xg_rows(inv)
    write_inventory_aliases(d, inv)
    coverage_truth_refresh = run_python_script(ROOT / 'scripts' / 'build_day_inventory_coverage_truth.py')
    report_status = 'ok' if matches else 'no_matches'
    if pipeline_errors:
        report_status = 'degraded' if matches else 'error'
    report = {
        'status': report_status,
        'pipeline_ok': not pipeline_errors,
        'pipeline_error_count': len(pipeline_errors),
        'pipeline_errors': pipeline_errors[:5],
        'inventory_path': str(inv_path),
        'inventory_selection': inventory_selection,
        'enrichment_merge': enrichment_merge,
        'coverage_truth_refresh': coverage_truth_refresh,
        'updated_at_utc': now.isoformat(),
        'date_local': d,
        'matches_total': len(matches),
        'real_xg_rows': real_xg_rows(inv),
        'min_odds_sources': min_odds_sources,
        'min_context_sources': min_context_sources,
        'coverage_pipeline_steps': pipeline_steps,
        'current_by_kickoff_window': current,
        'cumulative_high_watermark_by_kickoff_window': high_watermark,
        'missing_2plus_source_examples': samples_missing,
    }
    write_json(OUT, report)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
