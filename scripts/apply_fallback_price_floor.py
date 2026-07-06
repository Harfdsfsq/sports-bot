from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

OUT = Path('.data/exports/latest-fallback-price-floor-policy.json')


def main() -> int:
    os.environ.setdefault('CONTROLLED_FALLBACK_GLOBAL_MIN_ODDS', '1.75')
    os.environ.setdefault('CONTROLLED_FALLBACK_PREFERRED_MIN_ODDS', '1.80')
    payload = {
        'created_at_utc': datetime.now(timezone.utc).isoformat(),
        'status': 'installed',
        'global_min': os.environ.get('CONTROLLED_FALLBACK_GLOBAL_MIN_ODDS'),
        'preferred_min': os.environ.get('CONTROLLED_FALLBACK_PREFERRED_MIN_ODDS'),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
