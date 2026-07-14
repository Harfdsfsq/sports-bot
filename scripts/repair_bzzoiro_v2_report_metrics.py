from __future__ import annotations

"""Make Bzzoiro v2 runtime diagnostics visible to Telegram reports.

The source-matrix bridge intentionally kept the legacy report file name, but its
legacy stats block reported ``requests: 0`` even when the actual v2 provider had
fetched the event list.  That made Telegram say ``v2 req 0`` while the runtime
sidecar had ``v2_events_fetched`` and target/match diagnostics.

When the event-list request times out before any events are fetched, show a
conservative request estimate instead of zero so the report says provider timeout
rather than looking disabled.
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

EXPORT = Path('.data/exports')
PATHS = [
    EXPORT / 'latest-bzzoiro-v2-source-matrix-runtime.json',
    EXPORT / 'latest-bzzoiro-context-gap-finalizer.json',
]
OUT = EXPORT / 'latest-bzzoiro-v2-report-metrics-repair.json'


def load(path: Path) -> dict[str, Any]:
    try:
        if path.exists() and path.stat().st_size > 0:
            value = json.loads(path.read_text(encoding='utf-8', errors='replace'))
            return value if isinstance(value, dict) else {}
    except Exception:
        pass
    return {}


def dump(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')


def as_int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ''):
            return default
        if isinstance(value, (list, tuple, set, dict)):
            return len(value)
        return int(float(str(value)))
    except Exception:
        return default


def _attempt_requests(stats: dict[str, Any]) -> int:
    attempts = stats.get('runtime_day_window_patch_attempts')
    if isinstance(attempts, list):
        values = [as_int((x or {}).get('requests')) for x in attempts if isinstance(x, dict)]
        if values:
            return max(values)
    retry_attempts = as_int(stats.get('retry_attempts'))
    response_errors = as_int(stats.get('response_errors') or stats.get('errors'))
    if response_errors and retry_attempts:
        return retry_attempts + response_errors
    if response_errors:
        return response_errors
    return 0


def repair(path: Path) -> dict[str, Any]:
    payload = load(path)
    if not payload:
        return {'path': str(path), 'status': 'missing'}
    stats = payload.setdefault('stats', {})
    preview = payload.get('preview') if isinstance(payload.get('preview'), dict) else {}
    if not isinstance(stats, dict):
        stats = {}
        payload['stats'] = stats
    v2_events = as_int(stats.get('v2_events_fetched') or stats.get('events_fetched'))
    comparison = preview.get('odds_comparison_attachment') if isinstance(preview.get('odds_comparison_attachment'), dict) else {}
    comparison_attempts = as_int(stats.get('odds_comparison_attempted') or comparison.get('attempted'))
    timeout_estimate = _attempt_requests(stats)
    # Conservative lower-bound request count: event-list page(s), detail/comparison attempts, or failed timeout attempts.
    estimated_requests = max(as_int(stats.get('requests')), (1 if v2_events > 0 else 0) + comparison_attempts, timeout_estimate)
    changed = 0
    if estimated_requests and as_int(stats.get('requests')) < estimated_requests:
        stats['requests'] = estimated_requests
        changed += 1
    if v2_events and as_int(stats.get('events_fetched')) < v2_events:
        stats['events_fetched'] = v2_events
        changed += 1
    if comparison_attempts and as_int(stats.get('odds_comparison_attempted')) < comparison_attempts:
        stats['odds_comparison_attempted'] = comparison_attempts
        changed += 1
    if comparison and as_int(stats.get('odds_comparison_attached')) < as_int(comparison.get('attached')):
        stats['odds_comparison_attached'] = as_int(comparison.get('attached'))
        changed += 1
    if timeout_estimate and not v2_events:
        stats['diagnosis'] = stats.get('diagnosis') or 'bzzoiro_v2_event_list_timeout_or_empty'
        changed += 1
    stats['report_metrics_repaired'] = True
    stats['report_metrics_repaired_at_utc'] = datetime.now(timezone.utc).isoformat()
    dump(path, payload)
    return {
        'path': str(path),
        'status': 'ok',
        'changed': changed,
        'requests': stats.get('requests'),
        'v2_events_fetched': v2_events,
        'odds_comparison_attempted': comparison_attempts,
        'timeout_estimate': timeout_estimate,
        'diagnosis': stats.get('diagnosis'),
    }


def main() -> int:
    report = {'status': 'ok', 'created_at_utc': datetime.now(timezone.utc).isoformat(), 'files': [repair(path) for path in PATHS]}
    dump(OUT, report)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
