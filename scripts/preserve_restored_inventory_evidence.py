from __future__ import annotations

import argparse
import json
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.services.publication_thresholds import publish_min_context_sources, publish_min_odds_sources
from zoneinfo import ZoneInfo

UTC = timezone.utc
ROOT = Path('.').resolve()
DAY_DIR = ROOT / '.data' / 'day_inventory'
BACKUP_DIR = ROOT / '.data' / 'day_inventory_restored_backup'
EXPORT_DIR = ROOT / '.data' / 'exports'
REPORT = EXPORT_DIR / 'latest-day-inventory-evidence-preserve.json'
SUMMARY = EXPORT_DIR / 'latest-day-inventory-summary.json'
LIST_FIELDS = ['odds_sources', 'line_sources', 'books', 'price_confirmations', 'context_sources', 'context_confirmations', 'fixture_sources']
DICT_FIELDS = ['price_backfill', 'coverage_gaps']
COUNT_FIELDS = ['fixture_sources_count', 'independent_odds_sources_count', 'odds_sources_count', 'books_count', 'price_confirmation_sources_count', 'price_sources_count', 'context_sources_count', 'confirmation_sources_count']
SAMPLE_FIELDS = ['source_evidence_samples', 'odds_api_io_backfill_samples', 'context_source_projection_reasons']


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return default


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')


def tz() -> ZoneInfo:
    try:
        return ZoneInfo(os.getenv('APP_TIMEZONE') or os.getenv('TZ') or 'Europe/Moscow')
    except Exception:
        return ZoneInfo('Europe/Moscow')


def target_date() -> str:
    explicit = str(os.getenv('DAY_INVENTORY_TARGET_DATE') or '').strip()
    return explicit or datetime.now(UTC).astimezone(tz()).date().isoformat()


def as_int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ''):
            return default
        return int(float(value))
    except Exception:
        return default


def key_for(row: dict[str, Any]) -> str:
    key = str(row.get('match_key') or row.get('canonical_match_id') or '').strip()
    if key:
        return key
    home = re.sub(r'[^a-z0-9]+', '_', str(row.get('home_team') or '').lower()).strip('_')
    away = re.sub(r'[^a-z0-9]+', '_', str(row.get('away_team') or '').lower()).strip('_')
    start = str(row.get('kickoff_utc') or row.get('commence_time') or row.get('kickoff_local') or '')[:16]
    return f'{home}__{away}__{start}'


def listify(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    if isinstance(value, tuple):
        return [str(x).strip() for x in value if str(x).strip()]
    if isinstance(value, set):
        return [str(x).strip() for x in value if str(x).strip()]
    if isinstance(value, str) and value.strip():
        return [v.strip() for v in re.split(r'[,|;/]+', value) if v.strip()]
    return []


def uniq(values: list[Any]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or '').strip()
        if not text:
            continue
        low = text.lower()
        if low in seen:
            continue
        seen.add(low)
        out.append(text)
    return out


def price_count(row: dict[str, Any]) -> int:
    md = row.get('metadata') if isinstance(row.get('metadata'), dict) else {}
    return max(as_int(md.get('price_confirmation_sources_count')), as_int(md.get('price_sources_count')), len(row.get('price_confirmations') or []), len(row.get('books') or []))


def context_count(row: dict[str, Any]) -> int:
    md = row.get('metadata') if isinstance(row.get('metadata'), dict) else {}
    return max(as_int(md.get('context_sources_count')), as_int(md.get('confirmation_sources_count')), len(row.get('context_confirmations') or []), len(row.get('context_sources') or []))


def merge(dst: dict[str, Any], src: dict[str, Any], now_iso: str) -> bool:
    before = json.dumps(dst, ensure_ascii=False, sort_keys=True)
    for field in LIST_FIELDS:
        dst[field] = uniq(listify(dst.get(field)) + listify(src.get(field)))
    for field in DICT_FIELDS:
        sv = src.get(field)
        if isinstance(sv, dict):
            dv = dst.get(field) if isinstance(dst.get(field), dict) else {}
            merged = dict(dv)
            merged.update({k: v for k, v in sv.items() if v not in (None, '', [], {})})
            dst[field] = merged
    smd = src.get('metadata') if isinstance(src.get('metadata'), dict) else {}
    dmd = dst.get('metadata') if isinstance(dst.get('metadata'), dict) else {}
    for field in COUNT_FIELDS:
        dmd[field] = max(as_int(dmd.get(field)), as_int(smd.get(field)))
    for field in SAMPLE_FIELDS:
        if field not in dmd and smd.get(field):
            dmd[field] = smd[field]
        elif isinstance(dmd.get(field), list) and isinstance(smd.get(field), list):
            dmd[field] = (dmd[field] + smd[field])[:12]
    dmd['cached_evidence_restored_utc'] = now_iso
    dst['metadata'] = dmd
    pc = max(price_count(dst), price_count(src))
    cc = max(context_count(dst), context_count(src))
    min_price = publish_min_odds_sources()
    min_context = publish_min_context_sources()
    cov = dst.get('coverage') if isinstance(dst.get('coverage'), dict) else {}
    scov = src.get('coverage') if isinstance(src.get('coverage'), dict) else {}
    cov['odds'] = bool(cov.get('odds')) or bool(scov.get('odds')) or pc > 0
    cov['context'] = bool(cov.get('context')) or bool(scov.get('context')) or cc > 0
    cov['odds_2plus_sources'] = pc >= min_price
    cov['context_2plus_sources'] = cc >= min_context
    cov['ready_for_model'] = bool(cov.get('ready_for_model')) or bool(scov.get('ready_for_model')) or (pc > 0 and cc > 0)
    cov['ready_for_publish'] = bool(cov.get('ready_for_publish')) or bool(scov.get('ready_for_publish')) or (pc >= min_price and cc >= min_context)
    dst['coverage'] = cov
    ref = dst.get('refresh') if isinstance(dst.get('refresh'), dict) else {}
    sref = src.get('refresh') if isinstance(src.get('refresh'), dict) else {}
    for f in ('last_odds_refresh_utc', 'last_context_refresh_utc'):
        if sref.get(f):
            ref[f] = max(str(ref.get(f) or ''), str(sref[f])) or sref[f]
    if ref:
        dst['refresh'] = ref
    dst['last_enriched_at'] = max(str(dst.get('last_enriched_at') or ''), str(src.get('last_enriched_at') or ''), now_iso)
    return before != json.dumps(dst, ensure_ascii=False, sort_keys=True)


def recompute_counts(matches: list[dict[str, Any]], old: dict[str, Any], now_iso: str) -> dict[str, Any]:
    min_price = publish_min_odds_sources()
    min_context = publish_min_context_sources()
    counts = dict(old or {})
    price2 = context2 = odds_any = context_any = ready_model = ready_publish = 0
    for row in matches:
        pc = price_count(row)
        cc = context_count(row)
        cov = row.get('coverage') if isinstance(row.get('coverage'), dict) else {}
        odds_any += int(bool(cov.get('odds')) or pc > 0)
        context_any += int(bool(cov.get('context')) or cc > 0)
        price2 += int(pc >= min_price)
        context2 += int(cc >= min_context)
        ready_model += int(bool(cov.get('ready_for_model')))
        ready_publish += int(bool(cov.get('ready_for_publish')))
    counts.update({
        'matches_total': len(matches),
        'matches_with_odds': odds_any,
        'matches_with_context': context_any,
        'matches_with_2plus_price_confirmations': price2,
        'matches_with_2plus_odds_sources': price2,
        'matches_with_2plus_context_sources': context2,
        'matches_ready_for_model': ready_model,
        'matches_ready_for_publish': ready_publish,
        'matches_missing_price_2plus': max(0, len(matches) - price2),
        'matches_missing_context_2plus': max(0, len(matches) - context2),
        'cached_evidence_preserve_updated_utc': now_iso,
    })
    return counts


def backup() -> int:
    if BACKUP_DIR.exists():
        shutil.rmtree(BACKUP_DIR)
    if DAY_DIR.exists():
        shutil.copytree(DAY_DIR, BACKUP_DIR)
    files = len(list(BACKUP_DIR.glob('*.json'))) if BACKUP_DIR.exists() else 0
    report = {'status': 'ok', 'mode': 'backup', 'files': files, 'backup_dir': str(BACKUP_DIR), 'updated_at_utc': datetime.now(UTC).isoformat()}
    write_json(REPORT, report)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def restore() -> int:
    now_iso = datetime.now(UTC).isoformat()
    d = target_date()
    cur_path = DAY_DIR / f'{d}.json'
    bak_path = BACKUP_DIR / f'{d}.json'
    cur = load_json(cur_path, {})
    bak = load_json(bak_path, {})
    if not isinstance(cur, dict) or not isinstance(bak, dict):
        report = {'status': 'skipped', 'mode': 'restore', 'reason': 'missing_current_or_backup', 'current': str(cur_path), 'backup': str(bak_path), 'updated_at_utc': now_iso}
        write_json(REPORT, report)
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    cur_matches = [row for row in cur.get('matches', []) if isinstance(row, dict)]
    bak_matches = [row for row in bak.get('matches', []) if isinstance(row, dict) and (price_count(row) > 0 or context_count(row) > 0)]
    bak_by_key = {key_for(row): row for row in bak_matches}
    changed = restored = 0
    for row in cur_matches:
        src = bak_by_key.get(key_for(row))
        if not src:
            continue
        restored += 1
        changed += int(merge(row, src, now_iso))
    cur['matches'] = cur_matches
    cur['counts'] = recompute_counts(cur_matches, cur.get('counts') if isinstance(cur.get('counts'), dict) else {}, now_iso)
    cur['updated_at_utc'] = now_iso
    sources = cur.setdefault('sources', {})
    if isinstance(sources, dict):
        sources['cached_inventory_evidence_preserve'] = {'updated_at_utc': now_iso, 'backup_evidence_matches': len(bak_matches), 'restored_matching_rows': restored, 'rows_changed': changed}
    for path in [cur_path, DAY_DIR / 'latest.json', DAY_DIR / 'current.json', DAY_DIR / 'today.json']:
        write_json(path, cur)
    summary = load_json(SUMMARY, {})
    if isinstance(summary, dict):
        summary['counts'] = cur.get('counts', {})
        summary['sources'] = dict(cur.get('sources') or {})
        summary['updated_at_utc'] = now_iso
        write_json(SUMMARY, summary)
    report = {'status': 'ok', 'mode': 'restore', 'date_local': d, 'backup_evidence_matches': len(bak_matches), 'current_matches': len(cur_matches), 'restored_matching_rows': restored, 'rows_changed': changed, 'counts': cur.get('counts', {}), 'updated_at_utc': now_iso}
    write_json(REPORT, report)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--mode', choices=['backup', 'restore'], required=True)
    args = parser.parse_args()
    return backup() if args.mode == 'backup' else restore()


if __name__ == '__main__':
    raise SystemExit(main())
