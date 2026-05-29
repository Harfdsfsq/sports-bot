from __future__ import annotations

"""Build HARIZON performance and rejected-near-miss ledgers.

No external API calls.  This script consolidates current run artifacts into two
append-only JSONL ledgers:
  * published performance ledger: what was sent, stake, tier, sources, CLV/result
    placeholders for later settlement;
  * rejected near-miss ledger: candidates that were close enough to study before
    changing thresholds.

It is intentionally conservative: it never changes publication decisions.
"""

import hashlib
import json
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

UTC = timezone.utc
ROOT = Path('.').resolve()
EXPORT = ROOT / '.data' / 'exports'
PERF_JSONL = ROOT / '.data' / 'performance-ledger.jsonl'
NEAR_JSONL = ROOT / '.data' / 'rejected-near-miss-ledger.jsonl'
PERF_SUMMARY = EXPORT / 'latest-performance-ledger-summary.json'
NEAR_SUMMARY = EXPORT / 'latest-rejected-near-miss-report.json'

PUBLISHED_PATHS = [
    EXPORT / 'latest-controlled-fallback-published-picks.json',
    EXPORT / 'latest-normalized-publication-payloads.json',
    EXPORT / 'latest-controlled-fallback-report.json',
    Path('artifacts/controlled-fallback-report.json'),
]
CANDIDATE_PATHS = [
    EXPORT / 'latest-controlled-fallback-report.json',
    EXPORT / 'latest-rescue-candidates.json',
    Path('artifacts/run-bot/latest-rescue-candidates.json'),
    Path('.logs/debug-last-run.json'),
]
SETTLEMENT_PATHS = [
    EXPORT / 'latest-settlement-review-report.json',
    EXPORT / 'latest-settlement-summary.json',
    Path('.data/state.json'),
]


def load_json(path: Path, default: Any = None) -> Any:
    try:
        if path.exists() and path.stat().st_size > 0:
            return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return default
    return default


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')


def as_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ''):
            return default
        return float(str(value).replace(',', '.'))
    except Exception:
        return default


def as_int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ''):
            return default
        return int(float(str(value).replace(',', '.')))
    except Exception:
        return default


def first_dict(*values: Any) -> dict[str, Any]:
    for value in values:
        if isinstance(value, dict):
            return value
    return {}


def rows_from(payload: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if not isinstance(payload, dict):
        return []
    for key in (
        'published_picks', 'picks', 'items', 'rows', 'selected_all', 'evaluated',
        'fallback_evaluated', 'candidates', 'latest_rescue_candidates',
        'debug_candidates_before_quality', 'candidates_before_quality',
    ):
        value = payload.get(key)
        if isinstance(value, list):
            rows.extend([x for x in value if isinstance(x, dict)])
        elif isinstance(value, dict):
            rows.append(value)
    selected = payload.get('selected')
    if isinstance(selected, dict):
        rows.append(selected)
    samples = payload.get('samples') if isinstance(payload.get('samples'), dict) else {}
    for key in ('fallback_evaluated', 'rescue_candidates', 'debug_candidates_before_quality'):
        value = samples.get(key)
        if isinstance(value, list):
            rows.extend([x for x in value if isinstance(x, dict)])
    debug = payload.get('debug') if isinstance(payload.get('debug'), dict) else {}
    for key in ('candidates_before_quality', 'debug_candidates_before_quality'):
        value = debug.get(key)
        if isinstance(value, list):
            rows.extend([x for x in value if isinstance(x, dict)])
    return rows


def metric(row: dict[str, Any], *keys: str) -> Any:
    metrics = row.get('metrics') if isinstance(row.get('metrics'), dict) else {}
    last = row.get('last_metrics') if isinstance(row.get('last_metrics'), dict) else {}
    diag = row.get('diagnostics') if isinstance(row.get('diagnostics'), dict) else {}
    quality = diag.get('quality') if isinstance(diag.get('quality'), dict) else {}
    controlled = diag.get('controlled_fallback') if isinstance(diag.get('controlled_fallback'), dict) else {}
    for key in keys:
        for container in (row, metrics, last, quality, controlled):
            if isinstance(container, dict) and container.get(key) not in (None, ''):
                return container.get(key)
    return None


def list_values(row: dict[str, Any], *keys: str) -> list[str]:
    out: list[str] = []
    for key in keys:
        value = metric(row, key)
        if isinstance(value, list):
            out.extend(str(x).strip() for x in value if str(x).strip())
        elif isinstance(value, dict):
            out.extend(str(k).strip() for k, v in value.items() if str(k).strip() and v not in (None, '', [], {}))
        elif isinstance(value, str) and value.strip():
            out.extend(x.strip() for x in value.replace('+', ',').replace('|', ',').split(',') if x.strip())
    seen: set[str] = set(); clean: list[str] = []
    for item in out:
        norm = item.lower().replace('-', '_').replace(' ', '_')
        if norm in {'oddsapiio', 'odds_api', 'odds_api_io_account1', 'odds_api_io_account2'}:
            norm = 'odds_api_io'
        if norm and norm not in seen:
            seen.add(norm); clean.append(norm)
    return clean


def candidate_id(row: dict[str, Any]) -> str:
    parts = [
        str(metric(row, 'match_key') or ''),
        str(metric(row, 'home_team', 'home') or '').lower(),
        str(metric(row, 'away_team', 'away') or '').lower(),
        str(metric(row, 'commence_time', 'kickoff_utc', 'start_time') or ''),
        str(metric(row, 'family', 'market_family') or '').lower(),
        str(metric(row, 'selection', 'pick') or '').lower(),
        str(metric(row, 'point', 'line') or ''),
        str(metric(row, 'price_used_for_ev', 'odds', 'selected_odds') or ''),
    ]
    raw = '|'.join(parts)
    return hashlib.sha1(raw.encode('utf-8', errors='ignore')).hexdigest()[:20]


def base_row(row: dict[str, Any]) -> dict[str, Any]:
    odds_sources = list_values(row, 'independent_odds_sources', 'odds_sources', 'line_sources')
    confirmations = list_values(row, 'confirmation_sources', 'context_sources', 'sources')
    books = list_values(row, 'books', 'bookmakers', 'bookmaker')
    movement = first_dict(metric(row, 'line_movement'), metric(row, 'line_movement_guard'))
    xg = first_dict(metric(row, 'xg_check'), metric(row, 'xg'), metric(row, 'market_sanity'))
    return {
        'id': candidate_id(row),
        'created_at_utc': datetime.now(UTC).isoformat(),
        'run_id': os.getenv('GITHUB_RUN_ID') or metric(row, 'run_id'),
        'match_key': metric(row, 'match_key'),
        'home_team': metric(row, 'home_team', 'home'),
        'away_team': metric(row, 'away_team', 'away'),
        'league_name': metric(row, 'league_name', 'league'),
        'kickoff_utc': metric(row, 'kickoff_utc', 'commence_time', 'start_time'),
        'family': metric(row, 'family', 'market_family'),
        'selection': metric(row, 'selection', 'pick'),
        'point': metric(row, 'point', 'line', 'total'),
        'price_published': as_float(metric(row, 'price_used_for_ev', 'selected_odds', 'odds'), 0.0),
        'closing_price': as_float(metric(row, 'closing_odds', 'closing_price'), 0.0),
        'clv_pct': as_float(metric(row, 'clv_pct'), 0.0),
        'ev_pct': as_float(metric(row, 'canonical_ev_pct', 'ev_pct'), 0.0),
        'edge_pp': as_float(metric(row, 'canonical_edge_pp', 'edge_pp', 'edge_pct'), 0.0),
        'quality': as_float(metric(row, 'quality_score', 'q'), 0.0),
        'confidence': as_float(metric(row, 'confidence'), 0.0),
        'tier': metric(row, 'tier', 'publication_tier', 'level'),
        'stake': as_float(metric(row, 'stake', 'stake_amount'), 0.0),
        'bankroll': as_float(metric(row, 'bankroll'), 0.0),
        'odds_sources': odds_sources,
        'odds_sources_count': len(set(odds_sources)) or as_int(metric(row, 'odds_sources_count'), 0),
        'confirmation_sources': confirmations,
        'confirmation_sources_count': len(set(confirmations)) or as_int(metric(row, 'confirmation_sources_count', 'sources_count'), 0),
        'bookmakers': books,
        'bookmakers_count': len(set(books)) or as_int(metric(row, 'books_count'), 0),
        'xg_agreement': metric(row, 'xg_agreement') or xg.get('passed') or xg.get('agreement'),
        'xg_home': as_float(metric(row, 'expected_home', 'xg_home'), 0.0),
        'xg_away': as_float(metric(row, 'expected_away', 'xg_away'), 0.0),
        'line_movement_status': movement.get('status') or metric(row, 'line_movement_status'),
        'line_movement_passed': bool(movement.get('passed')) if movement else None,
        'reject_reasons': list_values(row, 'reject_reasons', 'final_reject_reasons', 'reasons'),
        'raw': row,
    }


def append_unique_jsonl(path: Path, rows: list[dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    existing: set[str] = set()
    if path.exists():
        try:
            for line in path.read_text(encoding='utf-8').splitlines():
                if not line.strip():
                    continue
                obj = json.loads(line)
                if isinstance(obj, dict) and obj.get('id'):
                    existing.add(str(obj['id']))
        except Exception:
            pass
    added = 0
    with path.open('a', encoding='utf-8') as f:
        for row in rows:
            rid = str(row.get('id') or '')
            if not rid or rid in existing:
                continue
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + '\n')
            existing.add(rid)
            added += 1
    return added


def _published_rows_from_payload(path: Path, payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if not isinstance(payload, dict):
        return []
    if path.name == 'latest-controlled-fallback-report.json':
        if not (payload.get('published') or str(payload.get('status') or '').lower() == 'published'):
            return []
        if isinstance(payload.get('published_picks'), list):
            return [x for x in payload['published_picks'] if isinstance(x, dict)]
        if isinstance(payload.get('selected_all'), list) and payload.get('selected_all'):
            return [x for x in payload['selected_all'] if isinstance(x, dict)]
        if isinstance(payload.get('selected'), dict):
            return [payload['selected']]
        return []
    for key in ('published_picks', 'picks', 'items', 'rows'):
        value = payload.get(key)
        if isinstance(value, list):
            return [x for x in value if isinstance(x, dict)]
        if isinstance(value, dict):
            return [value]
    if isinstance(payload.get('selected'), dict) and (payload.get('published') or payload.get('status') == 'published'):
        return [payload['selected']]
    return []


def performance_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in PUBLISHED_PATHS:
        payload = load_json(path)
        for row in _published_rows_from_payload(path, payload):
            br = base_row(row)
            br['ledger_type'] = 'published_pick'
            br['published_at_utc'] = metric(row, 'published_at_utc', 'telegram_sent_at_utc') or br['created_at_utc']
            br['settlement_status'] = metric(row, 'settlement_status') or 'open'
            br['result'] = metric(row, 'result', 'settlement_result')
            br['profit_loss'] = as_float(metric(row, 'profit_loss', 'pnl'), 0.0)
            br['publication_reason'] = metric(row, 'publication_reason') or 'controlled_fallback'
            rows.append(br)
    # Dedupe in-memory too.
    seen: set[str] = set(); out: list[dict[str, Any]] = []
    for row in rows:
        if row['id'] in seen:
            continue
        seen.add(row['id']); out.append(row)
    return out


def near_miss_rows() -> list[dict[str, Any]]:
    min_ev = as_float(os.getenv('NEAR_MISS_LEDGER_MIN_EV_PCT'), 2.0)
    min_edge = as_float(os.getenv('NEAR_MISS_LEDGER_MIN_EDGE_PP'), 0.8)
    rows: list[dict[str, Any]] = []
    for path in CANDIDATE_PATHS:
        payload = load_json(path)
        for row in rows_from(payload):
            br = base_row(row)
            reasons = [r.lower() for r in br.get('reject_reasons') or []]
            if not reasons:
                continue
            ev = float(br.get('ev_pct') or 0.0)
            edge = float(br.get('edge_pp') or 0.0)
            quality = float(br.get('quality') or 0.0)
            # Keep candidates useful for threshold studies; exclude hard-bad value.
            if ev < min_ev and edge < min_edge and quality < 65:
                continue
            br['ledger_type'] = 'rejected_near_miss'
            br['artifact_source'] = path.as_posix()
            br['study_bucket'] = classify_near_miss(br, reasons)
            br['would_have_been_stake'] = br.get('stake') or 0.0
            rows.append(br)
    seen: set[str] = set(); out: list[dict[str, Any]] = []
    for row in rows:
        if row['id'] in seen:
            continue
        seen.add(row['id']); out.append(row)
    return out


def classify_near_miss(row: dict[str, Any], reasons: list[str]) -> str:
    text = ' '.join(reasons)
    if 'xg' in text or 'direction' in text:
        return 'xg_conflict'
    if 'confirmation' in text or 'sources below' in text:
        return 'source_confirmation_gap'
    if 'quality' in text or 'confidence' in text:
        return 'quality_confidence_gap'
    if 'edge' in text or 'ev' in text or 'value' in text:
        return 'value_margin_gap'
    if 'line movement' in text or 'movement' in text:
        return 'line_movement_gap'
    return 'other'


def summarize_jsonl(path: Path) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    if path.exists():
        for line in path.read_text(encoding='utf-8').splitlines():
            try:
                obj = json.loads(line)
                if isinstance(obj, dict):
                    rows.append(obj)
            except Exception:
                continue
    by_tier = Counter(str(r.get('tier') or '').upper() or 'unknown' for r in rows)
    by_source_mix = Counter('+'.join(sorted(set(r.get('odds_sources') or []))) or 'unknown' for r in rows)
    by_bucket = Counter(str(r.get('study_bucket') or r.get('settlement_status') or 'unknown') for r in rows)
    settled = [r for r in rows if str(r.get('settlement_status') or '').lower() in {'won', 'lost', 'push', 'void', 'settled'} or r.get('result')]
    pnl = sum(as_float(r.get('profit_loss'), 0.0) for r in rows)
    stake = sum(as_float(r.get('stake'), 0.0) for r in rows)
    return {
        'rows_total': len(rows),
        'rows_settled_or_result_present': len(settled),
        'stake_total': round(stake, 3),
        'profit_loss_total': round(pnl, 3),
        'roi_pct': round((pnl / stake * 100.0), 3) if stake else 0.0,
        'by_tier': dict(by_tier.most_common()),
        'by_source_mix': dict(by_source_mix.most_common(12)),
        'by_bucket': dict(by_bucket.most_common(12)),
        'sample': rows[-10:],
    }


def main() -> int:
    if os.getenv('PERFORMANCE_LEDGER_ENABLED', 'true').lower() not in {'1', 'true', 'yes', 'on', 'force'}:
        return 0
    perf = performance_rows()
    near = near_miss_rows() if os.getenv('REJECTED_NEAR_MISS_LEDGER_ENABLED', 'true').lower() in {'1', 'true', 'yes', 'on', 'force'} else []
    added_perf = append_unique_jsonl(PERF_JSONL, perf)
    added_near = append_unique_jsonl(NEAR_JSONL, near)
    perf_summary = summarize_jsonl(PERF_JSONL)
    near_summary = summarize_jsonl(NEAR_JSONL)
    perf_summary.update({'status': 'ok', 'created_at_utc': datetime.now(UTC).isoformat(), 'added_this_run': added_perf, 'ledger_path': PERF_JSONL.as_posix()})
    near_summary.update({'status': 'ok', 'created_at_utc': datetime.now(UTC).isoformat(), 'added_this_run': added_near, 'ledger_path': NEAR_JSONL.as_posix()})
    write_json(PERF_SUMMARY, perf_summary)
    write_json(NEAR_SUMMARY, near_summary)
    print(json.dumps({'performance': perf_summary, 'near_miss': near_summary}, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
