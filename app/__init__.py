from __future__ import annotations

import atexit
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
EXPORT_DIR = ROOT / ".data" / "exports"
DAY_DIR = ROOT / ".data" / "day_inventory"
LIVE_ODDS = {"odds_api_io", "bzzoiro", "sportlogic"}


def _enabled(name: str, default: str = 'true') -> bool:
    return str(os.getenv(name, default)).strip().lower() in {'1', 'true', 'yes', 'on', 'force'}


def _is_run_once() -> bool:
    argv = ' '.join(str(x) for x in sys.argv).lower()
    return 'run-once' in argv and ('app.cli' in argv or 'cli.py' in argv or '-m' in argv)


def _is_report_only_run() -> bool:
    return (
        _enabled('DAILY_REPORT_ENABLED', 'false')
        and not _enabled('PREDICTION_PUBLICATION_ENABLED', 'false')
        and not _enabled('CONTROLLED_FALLBACK_ENABLED', 'false')
    )


def _load_json(path: Path, default: Any) -> Any:
    try:
        if path.exists() and path.stat().st_size > 0:
            return json.loads(path.read_text(encoding='utf-8', errors='replace'))
    except Exception:
        pass
    return default


def _write_json(path: Path, payload: Any) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    except Exception:
        pass


def _list(value: Any) -> list[str]:
    if isinstance(value, dict):
        return [str(k).strip() for k in value.keys() if str(k).strip()]
    if isinstance(value, (list, tuple, set)):
        return [str(x).strip() for x in value if str(x).strip()]
    if isinstance(value, str) and value.strip():
        return [x.strip() for x in re.split(r'[,|;/]+', value) if x.strip()]
    return []


def _norm_source(value: Any) -> str:
    text = re.sub(r'[^a-z0-9]+', '_', str(value or '').strip().lower()).strip('_')
    return {
        'oddsapiio': 'odds_api_io',
        'odds_api': 'odds_api_io',
        'odds_api_io_account1': 'odds_api_io',
        'odds_api_io_account2': 'odds_api_io',
        'bzzoiro_v2': 'bzzoiro',
        'bzzoiro_current_odds': 'bzzoiro',
        'sport_logic': 'sportlogic',
    }.get(text, text)


def _as_int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ''):
            return default
        return int(float(str(value)))
    except Exception:
        return default


def _parse_dt(value: Any):
    try:
        text = str(value or '').strip()
        if not text:
            return None
        if text.endswith('Z'):
            text = text[:-1] + '+00:00'
        if 'T' in text and '+' not in text:
            text += '+00:00'
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _future(row: dict[str, Any]) -> bool:
    dt = _parse_dt(row.get('kickoff_utc') or row.get('commence_time') or row.get('start_time') or row.get('event_date'))
    if dt is None:
        return True
    return (dt - datetime.now(timezone.utc)).total_seconds() >= -240


def _rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if not isinstance(payload, dict):
        return []
    out: list[dict[str, Any]] = []
    for key in ('matches', 'rows', 'gap_examples', 'gap_sample', 'core_gap_sample'):
        value = payload.get(key)
        if isinstance(value, list):
            out.extend(x for x in value if isinstance(x, dict))
        elif isinstance(value, dict):
            out.extend(x for x in value.values() if isinstance(x, dict))
    return out


def _key(row: dict[str, Any]) -> str:
    return str(row.get('match_key') or row.get('canonical_match_id') or '').strip()


def _odds_count(row: dict[str, Any]) -> int:
    values = _list(row.get('odds_sources')) + _list(row.get('line_sources'))
    cov = row.get('coverage') if isinstance(row.get('coverage'), dict) else {}
    values += _list(cov.get('odds_sources'))
    live = {_norm_source(x) for x in values if _norm_source(x) in LIVE_ODDS}
    if live:
        return len(live)
    return _as_int(row.get('odds_source_count') or row.get('odds_sources_count'))


def _context_count(row: dict[str, Any]) -> int:
    values = _list(row.get('context_sources')) + _list(row.get('context_confirmations'))
    clean = []
    for value in values:
        item = _norm_source(value)
        if item in {'', 'market', 'ensemble', 'odds_api_io', 'line_history'}:
            continue
        if re.match(r'^context_(source|confirmation)_\d+$', item):
            continue
        clean.append(item)
    if clean:
        return len(set(clean))
    return _as_int(row.get('context_source_count') or row.get('context_sources_count'))


def _needs_bzzoiro(row: dict[str, Any]) -> bool:
    if not isinstance(row, dict) or not _future(row):
        return False
    missing = {str(x).lower() for x in _list(row.get('missing')) + _list(row.get('tier_a_missing'))}
    return (
        _as_int(row.get('core_odds_needed') or row.get('odds_needed')) > 0
        or _as_int(row.get('core_context_needed') or row.get('context_needed')) > 0
        or _odds_count(row) < 2
        or _context_count(row) < 2
        or 'independent_odds_sources' in missing
        or 'context_sources' in missing
    )


def _prime_bzzoiro_source_matrix_plan() -> None:
    if not _enabled('HARIZON_BZZOIRO_V2_SOURCE_MATRIX_BOOTSTRAP_ENABLED'):
        return
    limit = max(1, _as_int(os.getenv('BZZOIRO_SCOPE_TARGET_LIMIT') or os.getenv('BZZOIRO_V2_SOURCE_MATRIX_TARGET_LIMIT') or 180, 180))
    sources = [
        ('progressive_existing', _rows(_load_json(EXPORT_DIR / 'latest-progressive-coverage-plan.json', {}))),
        ('coverage_planner', _rows(_load_json(EXPORT_DIR / 'latest-coverage-planner.json', {}))),
        ('coverage_truth', _rows(_load_json(EXPORT_DIR / 'latest-day-inventory-coverage-truth.json', {}))),
        ('today', _rows(_load_json(DAY_DIR / 'today.json', {}))),
        ('current', _rows(_load_json(DAY_DIR / 'current.json', {}))),
        ('latest', _rows(_load_json(DAY_DIR / 'latest.json', {}))),
    ]
    selected: dict[str, dict[str, Any]] = {}
    counts: dict[str, int] = {}
    for name, rows in sources:
        added = 0
        for row in rows:
            if not _needs_bzzoiro(row):
                continue
            key = _key(row)
            if not key or key in selected:
                continue
            selected[key] = dict(row)
            added += 1
            if len(selected) >= limit:
                break
        counts[name] = added
        if len(selected) >= limit:
            break
    rows = list(selected.values())
    payload = {
        'created_at_utc': datetime.now(timezone.utc).isoformat(),
        'status': 'ok' if rows else 'no_gap_rows',
        'limit': limit,
        'core_gap_sample': rows,
        'gap_sample': rows,
        'source_counts': counts,
        'sample_keys': list(selected)[:25],
        'note': 'Primed before Bzzoiro v2 source-matrix install so the provider targets live inventory gaps, not a stale/empty progressive plan.',
    }
    _write_json(EXPORT_DIR / 'latest-progressive-coverage-plan.json', payload)
    _write_json(EXPORT_DIR / 'latest-bzzoiro-source-matrix-plan-prime.json', payload)


def _sync_publication_ledger_before_cli() -> None:
    if not _enabled('HARIZON_PUBLICATION_LEDGER_BOOTSTRAP_SYNC_ENABLED'):
        return
    try:
        from scripts.sync_publication_ledger import sync_bets
        sync_bets()
    except Exception:
        pass


def _sync_publication_ledger_after_cli() -> None:
    if not _enabled('HARIZON_PUBLICATION_LEDGER_BOOTSTRAP_SYNC_ENABLED'):
        return
    try:
        from scripts.sync_publication_ledger import main as sync_main
        sync_main()
    except Exception:
        pass


def _send_past_predictions_report_after_cli() -> None:
    if not _enabled('PAST_PREDICTIONS_REPORT_AUTOSEND_ENABLED', 'false'):
        return
    if not _is_report_only_run():
        return
    try:
        from scripts import send_past_predictions_report
        old_argv = list(sys.argv)
        argv = ['send_past_predictions_report.py', '--all', '--send-telegram', '--force']
        sys.argv = argv
        try:
            send_past_predictions_report.main()
        finally:
            sys.argv = old_argv
    except Exception:
        pass


def _install_bzzoiro_v2_source_matrix() -> None:
    if not _enabled('HARIZON_BZZOIRO_V2_SOURCE_MATRIX_BOOTSTRAP_ENABLED'):
        return
    _prime_bzzoiro_source_matrix_plan()
    try:
        from app.services.bzzoiro_v2_source_matrix_runtime_patch import install
        install()
    except Exception:
        pass


def _run_bzzoiro_offer_bridge_after_cli() -> None:
    if not _enabled('HARIZON_BZZOIRO_OFFER_OVERLAP_BRIDGE_ENABLED'):
        return
    try:
        from scripts.bridge_bzzoiro_offer_overlap import main as bridge_main
        bridge_main()
    except Exception:
        pass
    try:
        from scripts.repair_bzzoiro_overlap_inventory_sources import main as repair_main
        repair_main()
    except Exception:
        pass


if _is_run_once():
    _sync_publication_ledger_before_cli()
    _install_bzzoiro_v2_source_matrix()
    atexit.register(_run_bzzoiro_offer_bridge_after_cli)
    atexit.register(_sync_publication_ledger_after_cli)
    atexit.register(_send_past_predictions_report_after_cli)
