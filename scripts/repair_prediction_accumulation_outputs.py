from __future__ import annotations

"""Repair prediction accumulation outputs after ledger/calibration builders.

The accumulation stage should store actual forecast candidates: rows that reached
value/fallback/quality/publication diagnostics.  Some API coverage audit rows are
useful for coverage truth but are not predictions yet.  If they enter the ledger,
they inflate current-run rows and produce false "missing core metrics" warnings.

This postprocess is intentionally read-only with respect to model decisions: it
only cleans analytics artifacts after ``update_prediction_ledger.py`` has run.
"""

import json
import os
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

UTC = timezone.utc
ROOT = Path('.').resolve()
EXPORT_DIR = ROOT / '.data' / 'exports'
LEDGER = ROOT / '.data' / 'prediction-ledger.jsonl'
SUMMARY = EXPORT_DIR / 'latest-prediction-ledger-summary.json'
CALIBRATION = EXPORT_DIR / 'latest-prediction-calibration-audit.json'
REPORT = EXPORT_DIR / 'latest-prediction-accumulation-repair.json'

PREDICTION_STAGES = {
    'before_quality',
    'after_quality',
    'value_patch',
    'fallback',
    'quality_relief',
    'normalized_publication',
}
CORE_METRIC_FIELDS = ('home_team', 'away_team', 'odds', 'ev_pct', 'edge_pp')


def load_json(path: Path, default: Any) -> Any:
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
        return payload
    except Exception:
        return default


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')


def as_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(x) for x in value if str(x)]
    if isinstance(value, str) and value.strip():
        return [x.strip() for x in re.split(r'[,|;/]+', value) if x.strip()]
    return []


def as_float(value: Any) -> float | None:
    try:
        if value in (None, ''):
            return None
        return float(str(value).replace(',', '.'))
    except Exception:
        return None


def stage_list(value: Any) -> list[str]:
    if isinstance(value, dict):
        return [str(k) for k, v in value.items() if bool(v)]
    return as_list(value)


def has_prediction_stage(row: dict[str, Any]) -> bool:
    return bool(PREDICTION_STAGES.intersection(stage_list(row.get('stage_seen'))))


def is_coverage_only_ledger_row(row: dict[str, Any]) -> bool:
    stages = set(stage_list(row.get('stage_seen')))
    if not stages:
        return False
    if stages - {'api_coverage'}:
        return False
    if row.get('status') == 'published':
        return False
    # API-only rows with a full edge and explicit reasons may be useful as a
    # rejected prediction.  Rows without edge or reasons are just coverage audit.
    return as_float(row.get('edge_pp')) is None or not as_list(row.get('reasons'))


def is_coverage_only_calibration_row(row: dict[str, Any]) -> bool:
    stages = set(stage_list(row.get('stage_seen')))
    if not stages:
        return False
    if stages - {'api_coverage'}:
        return False
    has_edge = as_float(row.get('edge_before_pp')) is not None or as_float(row.get('edge_after_pp')) is not None
    has_reasons = bool(as_list(row.get('reasons')))
    return not has_edge and not has_reasons


def read_ledger_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not LEDGER.exists():
        return rows
    for line in LEDGER.read_text(encoding='utf-8').splitlines():
        try:
            obj = json.loads(line)
            if isinstance(obj, dict):
                rows.append(obj)
        except Exception:
            continue
    return rows


def write_ledger_rows(rows: list[dict[str, Any]]) -> None:
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    LEDGER.write_text(''.join(json.dumps(row, ensure_ascii=False, sort_keys=True) + '\n' for row in rows), encoding='utf-8')


def missing_core(row: dict[str, Any]) -> bool:
    return any(row.get(field) in (None, '') for field in CORE_METRIC_FIELDS)


def summarize(rows: list[dict[str, Any]], current_run_id: str, pruned: int) -> dict[str, Any]:
    by_status = Counter(str(row.get('status') or 'unknown') for row in rows)
    by_reason: Counter[str] = Counter()
    for row in rows:
        for reason in as_list(row.get('reasons')):
            by_reason[reason] += 1
    current = [row for row in rows if str(row.get('run_id') or '') == str(current_run_id)] if current_run_id else []
    return {
        'status': 'ok',
        'updated_at_utc': datetime.now(UTC).isoformat(),
        'ledger_path': str(LEDGER),
        'current_run_id': current_run_id,
        'total_rows': len(rows),
        'current_run_rows': len(current),
        'rows_missing_core_metrics_total': sum(1 for row in rows if missing_core(row)),
        'rows_missing_core_metrics_current_run': sum(1 for row in current if missing_core(row)),
        'coverage_only_rows_removed_current_run': pruned,
        'by_status': dict(by_status),
        'top_reasons': by_reason.most_common(30),
        'notes': [
            'Use this ledger for forward testing before trusting ROI.',
            'API coverage-only rows are kept in coverage/opportunity audits, not in the prediction ledger.',
            'quality/confidence are optional analytics fields; core metrics are identity + odds + EV + edge.',
        ],
    }


def repair_ledger(current_run_id: str) -> dict[str, Any]:
    rows = read_ledger_rows()
    kept: list[dict[str, Any]] = []
    removed: list[dict[str, Any]] = []
    for row in rows:
        if str(row.get('run_id') or '') == str(current_run_id) and is_coverage_only_ledger_row(row):
            removed.append(row)
        else:
            kept.append(row)
    if removed:
        write_ledger_rows(kept)
    summary = summarize(kept, current_run_id, len(removed))
    write_json(SUMMARY, summary)
    return {
        'input_rows': len(rows),
        'kept_rows': len(kept),
        'removed_current_run': len(removed),
        'removed_sample': [
            {
                'candidate_key': row.get('candidate_key'),
                'home_team': row.get('home_team'),
                'away_team': row.get('away_team'),
                'stage_seen': row.get('stage_seen'),
                'reason': 'api_coverage_only_not_prediction',
            }
            for row in removed[:10]
        ],
        'summary': summary,
    }


def repair_calibration() -> dict[str, Any]:
    payload = load_json(CALIBRATION, {})
    if not isinstance(payload, dict):
        return {'status': 'skipped', 'reason': 'missing_or_invalid'}
    rows = [row for row in payload.get('rows', []) if isinstance(row, dict)]
    kept = [row for row in rows if not is_coverage_only_calibration_row(row)]
    removed = len(rows) - len(kept)
    if removed:
        counts = dict(payload.get('counts') or {})
        counts['candidate_keys'] = len(kept)
        counts['rows_missing_core_metrics'] = sum(
            1 for row in kept
            if row.get('home_team') in (None, '') or row.get('away_team') in (None, '') or row.get('odds') in (None, '')
        )
        counts['coverage_only_rows_removed'] = removed
        counts['rows_with_quality'] = sum(1 for row in kept if row.get('quality') is not None)
        payload['counts'] = counts
        payload['rows'] = kept[:250]
        notes = list(payload.get('notes') or [])
        notes.append('API coverage-only rows were removed from calibration audit; see candidate opportunity audit for coverage gaps.')
        payload['notes'] = notes
        write_json(CALIBRATION, payload)
    return {
        'input_rows': len(rows),
        'kept_rows': len(kept),
        'removed_rows': removed,
    }


def main() -> int:
    summary_payload = load_json(SUMMARY, {}) if SUMMARY.exists() else {}
    current_run_id = str(os.getenv('GITHUB_RUN_ID') or summary_payload.get('current_run_id') or '').strip()
    if not current_run_id:
        current_run_id = datetime.now(UTC).strftime('%Y%m%d%H%M%S')
    ledger_result = repair_ledger(current_run_id)
    calibration_result = repair_calibration()
    report = {
        'status': 'ok',
        'created_at_utc': datetime.now(UTC).isoformat(),
        'current_run_id': current_run_id,
        'ledger': ledger_result,
        'calibration': calibration_result,
    }
    write_json(REPORT, report)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
