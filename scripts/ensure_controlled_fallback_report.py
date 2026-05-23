from __future__ import annotations

"""Create an explicit controlled-fallback artifact when the fallback step did not.

The report renderer should never have to guess whether fallback was skipped,
crashed, or produced zero candidates.  This script is intentionally conservative:
it does not publish anything and only writes a no-op diagnostic if the real
fallback artifact is missing.
"""

import json
from datetime import datetime, timezone
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


def main() -> int:
    if LATEST.exists() and LATEST.stat().st_size > 0:
        return 0
    debug = load_json(Path('.logs/debug-last-run.json'), {})
    summary = debug.get('summary') if isinstance(debug, dict) and isinstance(debug.get('summary'), dict) else {}
    candidates_before = debug.get('candidates_before_quality') if isinstance(debug, dict) and isinstance(debug.get('candidates_before_quality'), list) else []
    payload = {
        'created_at': datetime.now(UTC).isoformat(),
        'enabled': True,
        'published': False,
        'status': 'fallback_artifact_missing_after_step',
        'candidates_seen': 0,
        'evaluated': [],
        'pool_counts': {
            'debug_candidates_before_quality_available': len(candidates_before),
            'summary_candidates_before_quality': int(summary.get('candidates_before_quality') or summary.get('candidates_raw') or 0),
        },
        'diagnostic_note': 'publish_controlled_fallback.py did not create latest-controlled-fallback-report.json; no Telegram pick was sent by this fallback process.',
    }
    write_json(LATEST, payload)
    write_json(ARTIFACT, payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
