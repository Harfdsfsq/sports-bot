from __future__ import annotations

"""Create/refresh an explicit controlled-fallback diagnostic artifact.

The detailed report should never have to guess whether fallback was skipped,
crashed, produced zero candidates, or is only seeing a stale committed artifact.
This script is intentionally conservative: it does not publish anything and only
writes a no-op diagnostic when the real fallback artifact is missing or stale
relative to the fresh run debug payload.
"""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

UTC = timezone.utc
EXPORT = Path('.data/exports')
ARTIFACT = Path('artifacts/controlled-fallback-report.json')
LATEST = EXPORT / 'latest-controlled-fallback-report.json'


def load_json(path: Path, default: Any) -> Any:
    try:
        if path.exists() and path.stat().st_size > 0:
            return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        pass
    return default


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')


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


def payload_ts(payload: Any) -> datetime | None:
    if not isinstance(payload, dict):
        return None
    candidates = [
        payload.get('created_at'),
        payload.get('created_at_utc'),
        payload.get('reference_run_utc'),
        payload.get('updated_at'),
    ]
    summary = payload.get('summary') if isinstance(payload.get('summary'), dict) else {}
    candidates.extend([
        summary.get('current_time_utc'),
        summary.get('started_time_utc'),
        summary.get('current_time_local'),
        summary.get('started_time_local'),
    ])
    for value in candidates:
        dt = parse_dt(value)
        if dt is not None:
            return dt
    return None


def debug_reference_ts(debug: Any) -> datetime:
    ts = payload_ts(debug)
    return ts or datetime.now(UTC)


def is_fresh_enough(existing: Any, reference: datetime, max_minutes: int = 90) -> bool:
    ts = payload_ts(existing)
    if ts is None:
        return False
    return abs((reference - ts).total_seconds()) <= max_minutes * 60


def candidate_list_from_debug(debug: Any) -> list[Any]:
    if not isinstance(debug, dict):
        return []
    for key in ('candidates_before_quality', 'debug_candidates_before_quality', 'latest_rescue_candidates', 'rescue_candidates'):
        value = debug.get(key)
        if isinstance(value, list):
            return value
    return []


def main() -> int:
    debug = load_json(Path('.logs/debug-last-run.json'), {})
    reference = debug_reference_ts(debug)
    existing = load_json(LATEST, {})
    if isinstance(existing, dict) and existing and is_fresh_enough(existing, reference):
        # Mirror a fresh real fallback report into artifacts/run-bot if needed.
        write_json(ARTIFACT, existing)
        return 0

    summary = debug.get('summary') if isinstance(debug, dict) and isinstance(debug.get('summary'), dict) else {}
    candidates_before = candidate_list_from_debug(debug)
    stale_ts = payload_ts(existing)
    payload = {
        'created_at': reference.isoformat(),
        'created_by': 'ensure_controlled_fallback_report',
        'enabled': True,
        'published': False,
        'status': 'fallback_artifact_stale_or_missing_after_step',
        'candidates_seen': 0,
        'evaluated': [],
        'pool_counts': {
            'debug_candidates_before_quality_available': len(candidates_before),
            'summary_candidates_before_quality': int(summary.get('candidates_before_quality') or summary.get('candidates_raw') or 0),
            'summary_publishable': int(summary.get('publishable') or summary.get('publishable_candidates') or 0),
        },
        'freshness': {
            'reference_run_utc': reference.isoformat(),
            'previous_fallback_created_at': stale_ts.isoformat() if stale_ts else None,
            'previous_fallback_was_stale': bool(stale_ts and abs((reference - stale_ts).total_seconds()) > 90 * 60),
            'previous_fallback_missing': not bool(existing),
        },
        'diagnostic_note': 'Controlled fallback did not create a fresh latest-controlled-fallback-report.json for this run; no Telegram pick was sent by this fallback process.',
    }
    write_json(LATEST, payload)
    write_json(ARTIFACT, payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
