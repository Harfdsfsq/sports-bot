from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

UTC = timezone.utc
ROOT = Path('.').resolve()
EXPORT = ROOT / '.data' / 'exports'
BET_DIR = ROOT / '.data' / 'bets'
OUT_JSON = EXPORT / 'latest-run-report-ledger.json'
OUT_STATUS = EXPORT / 'latest-run-report-ledger-sync.json'
OUT_JSONL = BET_DIR / 'run_report_ledger.jsonl'


def load_json(path: Path, default: Any) -> Any:
    try:
        if path.exists() and path.stat().st_size > 0:
            return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        pass
    return default


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        if not path.exists():
            return rows
        for line in path.read_text(encoding='utf-8').splitlines():
            line = line.strip()
            if not line:
                continue
            item = json.loads(line)
            if isinstance(item, dict):
                rows.append(item)
    except Exception:
        pass
    return rows


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(''.join(json.dumps(row, ensure_ascii=False, sort_keys=True) + '\n' for row in rows), encoding='utf-8')


def safe_int(value: Any) -> int:
    try:
        return int(float(value)) if value not in (None, '') else 0
    except Exception:
        return 0


def build_row() -> dict[str, Any] | None:
    report = load_json(EXPORT / 'latest-harizon-telegram-run-report.json', {})
    fallback = load_json(EXPORT / 'latest-controlled-fallback-report.json', {})
    debug = load_json(ROOT / '.logs' / 'debug-last-run.json', {})
    day_summary = load_json(EXPORT / 'latest-day-inventory-summary.json', {})
    if not any(isinstance(x, dict) and x for x in (report, fallback, debug, day_summary)):
        return None
    summary = debug.get('summary') if isinstance(debug.get('summary'), dict) else {}
    coverage = report.get('coverage') if isinstance(report.get('coverage'), dict) else {}
    funnel = report.get('funnel') if isinstance(report.get('funnel'), dict) else {}
    counts = day_summary.get('counts') if isinstance(day_summary.get('counts'), dict) else {}
    created = report.get('created_at_utc') or fallback.get('created_at') or fallback.get('created_at_utc') or summary.get('current_time_utc') or datetime.now(UTC).isoformat()
    published_count = safe_int(funnel.get('published_count')) or (1 if isinstance(fallback, dict) and fallback.get('published') else 0)
    return {
        'created_at_utc': created,
        'updated_at_utc': datetime.now(UTC).isoformat(),
        'source': 'run-bot',
        'github_run_id': report.get('github_run_id') or report.get('run_id') or fallback.get('github_run_id'),
        'fallback_status': fallback.get('status') if isinstance(fallback, dict) else None,
        'fallback_published': bool(fallback.get('published')) if isinstance(fallback, dict) else False,
        'summary': {
            'matches_seen': safe_int(coverage.get('matches_seen') or counts.get('matches_seen_latest_run') or summary.get('matches_seen')),
            'matches_with_offers': safe_int(coverage.get('matches_with_offers') or counts.get('runtime_matches_with_odds_last_run') or summary.get('matches_with_offers')),
            'contexts_built': safe_int(coverage.get('matches_with_context') or counts.get('runtime_matches_with_context_last_run') or summary.get('contexts_built')),
            'candidates_raw': safe_int(funnel.get('raw_candidates') or summary.get('candidates_raw')),
            'candidates_before_quality': safe_int(funnel.get('candidates_before_quality') or summary.get('candidates_before_quality')),
            'candidates_publishable': safe_int(funnel.get('publishable_candidates') or summary.get('candidates_publishable')),
            'published': published_count,
            'published_to_telegram': published_count,
            'telegram_messages_sent': published_count,
            'status': report.get('status') or summary.get('status') or 'ok',
        },
    }


def row_key(row: dict[str, Any]) -> str:
    raw = str(row.get('github_run_id') or '') + '|' + str(row.get('created_at_utc') or '')[:16]
    if not raw.strip('|'):
        raw = json.dumps(row, ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(raw.encode('utf-8')).hexdigest()


def main() -> int:
    row = build_row()
    existing: list[dict[str, Any]] = []
    payload = load_json(OUT_JSON, [])
    if isinstance(payload, list):
        existing.extend(x for x in payload if isinstance(x, dict))
    existing.extend(load_jsonl(OUT_JSONL))
    by_key = {row_key(item): item for item in existing if isinstance(item, dict)}
    appended = False
    if row is not None:
        by_key[row_key(row)] = row
        appended = True
    rows = sorted(by_key.values(), key=lambda item: str(item.get('created_at_utc') or ''))
    write_json(OUT_JSON, rows)
    write_jsonl(OUT_JSONL, rows)
    status = {'status': 'ok', 'created_at_utc': datetime.now(UTC).isoformat(), 'run_appended': appended, 'run_ledger_rows': len(rows), 'json_path': str(OUT_JSON), 'jsonl_path': str(OUT_JSONL)}
    write_json(OUT_STATUS, status)
    print(json.dumps(status, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
