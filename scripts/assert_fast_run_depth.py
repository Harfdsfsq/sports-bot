from __future__ import annotations

"""Write a compact diagnostic if fast mode starves market depth.

This is a warning-only guard.  It never changes publication logic and does not
fail the workflow by default.
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

UTC = timezone.utc
EXPORT = Path('.data/exports')
OUT = EXPORT / 'latest-fast-run-depth-check.json'


def as_int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ''):
            return default
        return int(float(str(value)))
    except Exception:
        return default


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


def main() -> int:
    report = load_json(EXPORT / 'latest-harizon-telegram-run-report.json', {})
    api = report.get('api') if isinstance(report, dict) and isinstance(report.get('api'), dict) else {}
    coverage = report.get('coverage') if isinstance(report, dict) and isinstance(report.get('coverage'), dict) else {}
    odds = api.get('odds_api_io') if isinstance(api.get('odds_api_io'), dict) else {}
    bzz = api.get('bzzoiro') if isinstance(api.get('bzzoiro'), dict) else {}
    warnings: list[str] = []
    if as_int(odds.get('odds_req')) < as_int(os.getenv('FAST_RUN_MIN_ODDS_REQUESTS_WARN'), 12):
        warnings.append('odds_api_io_request_depth_too_low')
    if as_int(coverage.get('matches_with_2plus_books')) <= 0:
        warnings.append('zero_matches_with_2plus_books')
    if as_int(bzz.get('v2_odds_resources')) <= 0 and as_int(bzz.get('secondary_offers_added')) <= 0:
        warnings.append('zero_bzzoiro_secondary_odds')
    payload = {
        'created_at_utc': datetime.now(UTC).isoformat(),
        'warnings': warnings,
        'ok': not warnings,
        'observed': {
            'odds_api_io_odds_req': as_int(odds.get('odds_req')),
            'matches_with_2plus_books': as_int(coverage.get('matches_with_2plus_books')),
            'bzzoiro_secondary_offers': as_int(bzz.get('secondary_offers_added')),
            'bzzoiro_v2_odds': as_int(bzz.get('v2_odds_resources')),
        },
    }
    write_json(OUT, payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    if warnings and str(os.getenv('FAST_RUN_DEPTH_CHECK_FAIL') or '').lower() in {'1', 'true', 'yes', 'on'}:
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
