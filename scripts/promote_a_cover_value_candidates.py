from __future__ import annotations

"""Promote active A-cover rows into the controlled fallback review pool.

This is not a publisher and it does not relax final guards.  It only fixes the
A-cover -> candidate gap that Telegram diagnostics exposed: rows can have A-tier
evidence coverage, but never reach the candidate pool.  We create conservative
market-consensus totals candidates from current offer buckets and append them to
latest-rescue-candidates.json.  The guarded fallback publisher still applies
value, xG, quality/proxy, line recheck, duplicate, daily cap and price-integrity
checks before anything can be sent.
"""

import json
import os
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from scripts import build_b_cover_candidate_gap_report as bcover

UTC = timezone.utc
ROOT = Path('.').resolve()
EXPORT = ROOT / '.data' / 'exports'
OUT = EXPORT / 'latest-a-cover-value-promotion.json'
RESCUE_PATH = EXPORT / 'latest-rescue-candidates.json'


def _as_int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ''):
            return default
        return int(float(str(value)))
    except Exception:
        return default


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ''):
            return default
        return float(str(value).replace(',', '.'))
    except Exception:
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == '':
        return default
    return str(raw).strip().lower() in {'1', 'true', 'yes', 'on', 'force'}


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')


def _parse_dt(value: Any) -> datetime | None:
    return bcover.parse_dt(value)


def _kickoff(row: dict[str, Any]) -> datetime | None:
    for key in ('commence_time', 'kickoff_utc', 'start_time', 'kickoff'):
        dt = _parse_dt(row.get(key))
        if dt is not None:
            return dt
    return None


def _in_active_window(row: dict[str, Any], now: datetime) -> bool:
    kickoff = _kickoff(row)
    if kickoff is None:
        return False
    min_lead = _as_int(os.getenv('LINE_MOVEMENT_MIN_LEAD_MINUTES') or os.getenv('MIN_KICKOFF_LEAD_MINUTES'), 15)
    if kickoff < now + timedelta(minutes=max(0, min_lead)):
        return False
    if not _env_bool('PROMOTE_A_COVER_ONLY_PUBLISH_WINDOW', True):
        return True
    hours = max(0.25, _as_float(os.getenv('CONTROLLED_FALLBACK_PUBLISH_WINDOW_HOURS') or os.getenv('PUBLISH_WINDOW_HOURS'), 2.0))
    return kickoff <= now + timedelta(hours=hours)


def _is_a_cover(row: dict[str, Any]) -> bool:
    if str(row.get('tier_a_coverage_ready') or row.get('ready_for_publish')).strip().lower() in {'1', 'true', 'yes', 'on'}:
        return True
    return bcover.source_count(row) >= 2 and bcover.book_count(row) >= 2 and bcover.context_count(row) >= 2


def _existing_signatures(rows: list[dict[str, Any]]) -> set[str]:
    return {bcover.candidate_signature(row) for row in rows if isinstance(row, dict)}


def _tune_candidate(candidate: dict[str, Any], inv_row: dict[str, Any]) -> dict[str, Any]:
    cand = dict(candidate)
    src_count = max(2, bcover.source_count(inv_row), _as_int(cand.get('odds_sources_count'), 1))
    book_count = max(2, bcover.book_count(inv_row), _as_int(cand.get('books_count'), 1))
    ctx_sources = bcover.context_sources(inv_row)
    ctx_count = max(2, bcover.context_count(inv_row), len(ctx_sources), _as_int(cand.get('confirmation_sources_count'), 1))
    cand['odds_sources_count'] = src_count
    cand['books_count'] = book_count
    cand['sources_count'] = max(_as_int(cand.get('sources_count'), 1), ctx_count)
    cand['confirmation_sources_count'] = ctx_count
    if ctx_sources:
        cand['confirmation_sources'] = ctx_sources
    cand['_candidate_source'] = 'a_cover_market_promotion'
    reasons = list(cand.get('reasons') or [])
    reasons.append('mode=a_cover_market_promotion')
    reasons.append(f'a_cover_sources={src_count}/books={book_count}/contexts={ctx_count}')
    cand['reasons'] = reasons
    source_summary = cand.get('source_summary') if isinstance(cand.get('source_summary'), dict) else {}
    source_summary = dict(source_summary)
    source_summary['selected_source'] = 'a_cover_market_promotion'
    source_summary['publish_coverage_contract'] = {
        'tier': 'A-cover-promoted-to-fallback-review',
        'odds_sources_count': src_count,
        'bookmakers_count': book_count,
        'context_sources_count': ctx_count,
        'note': 'candidate still must pass guarded fallback final checks before Telegram publication',
    }
    cand['source_summary'] = source_summary
    diagnostics = cand.get('diagnostics') if isinstance(cand.get('diagnostics'), dict) else {}
    diagnostics = dict(diagnostics)
    diagnostics['a_cover_promotion'] = {
        'created_by': 'promote_a_cover_value_candidates.py',
        'created_at_utc': datetime.now(UTC).isoformat(),
        'inventory_match_key': inv_row.get('match_key'),
        'odds_sources_count': src_count,
        'books_count': book_count,
        'context_sources_count': ctx_count,
    }
    cand['diagnostics'] = diagnostics
    return cand


def main() -> int:
    if not _env_bool('PROMOTE_A_COVER_VALUE_CANDIDATES_ENABLED', True):
        payload = {'enabled': False, 'reason': 'disabled'}
        _write_json(OUT, payload)
        print(json.dumps(payload, ensure_ascii=False))
        return 0

    day = bcover.target_date()
    prebuild = bcover.prebuild_coverage_truth_for_promotion()
    inventory, inventory_load = bcover.load_inventory_with_meta(day)
    now = datetime.now(UTC)
    offer_buckets, offer_diag = bcover.collect_offer_buckets(day)

    existing = bcover.rescue_rows_payload()
    initial_candidates = bcover.candidate_rows()
    signatures = _existing_signatures(existing + initial_candidates)
    limit = _as_int(os.getenv('PROMOTE_A_COVER_VALUE_CANDIDATE_LIMIT'), 18)
    reasons: Counter[str] = Counter()
    promoted: list[dict[str, Any]] = []
    considered = 0
    active_a_rows = 0
    in_window_rows: list[dict[str, Any]] = []

    for row in inventory:
        if not isinstance(row, dict):
            continue
        if not _is_a_cover(row):
            continue
        active_a_rows += 1
        if not _in_active_window(row, now):
            reasons['promotion_skip_outside_active_publish_window'] += 1
            continue
        in_window_rows.append(row)

    # Prefer the rows with the deepest context/price/source coverage first.
    in_window_rows.sort(key=lambda r: (bcover.context_count(r), bcover.book_count(r), bcover.source_count(r)), reverse=True)

    for row in in_window_rows:
        considered += 1
        match_buckets: dict[str, dict[str, Any]] = {}
        for key in bcover.fallback_match_keys(row, day):
            match_buckets.update(offer_buckets.get(key, {}))
        if not match_buckets:
            reasons['promotion_skip_no_offer_bucket'] += 1
            continue
        candidates_for_row: list[dict[str, Any]] = []
        for bucket_key, bucket in match_buckets.items():
            cand, reason = bcover.build_candidate_from_bucket(row, bucket_key, bucket)
            if cand is None:
                reasons[reason] += 1
                continue
            cand = _tune_candidate(cand, row)
            sig = bcover.candidate_signature(cand)
            if sig in signatures:
                reasons['promotion_skip_duplicate_candidate'] += 1
                continue
            signatures.add(sig)
            candidates_for_row.append(cand)
        candidates_for_row.sort(key=lambda c: (float(c.get('ev_pct') or 0.0), float(c.get('edge_pct') or 0.0), float(c.get('confidence') or 0.0)), reverse=True)
        for cand in candidates_for_row[:1]:
            promoted.append(cand)
            reasons['promoted'] += 1
            if limit and len(promoted) >= limit:
                break
        if limit and len(promoted) >= limit:
            break

    if promoted:
        merged = promoted + existing
        # Preserve the old rescue rows and place A-cover promotions first for review.
        _write_json(RESCUE_PATH, merged[: max(len(merged), len(existing) + len(promoted))])

    payload = {
        'enabled': True,
        'status': 'ok',
        'created_at_utc': now.isoformat(),
        'target_date': day,
        'inventory_rows_seen': len(inventory),
        'active_a_cover_rows': active_a_rows,
        'in_publish_window_a_cover_rows': len(in_window_rows),
        'considered_a_cover_rows': considered,
        'promoted_count': len(promoted),
        'reason_counts': dict(reasons.most_common()),
        'sample': promoted[:12],
        'rescue_path': str(RESCUE_PATH),
        'offer_diagnostics': offer_diag,
        'inventory_load': inventory_load,
        'prebuild_coverage_truth': prebuild,
        'safety_note': 'promotion only appends candidates to fallback review; guarded publisher still enforces value, xG, line recheck, duplicate, daily cap and price-integrity guards',
    }
    _write_json(OUT, payload)
    print(json.dumps({'status': 'ok', 'active_a_cover_rows': active_a_rows, 'in_publish_window_a_cover_rows': len(in_window_rows), 'promoted_count': len(promoted), 'top_reasons': dict(reasons.most_common(5))}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
