from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

OUT = Path('.data/exports/latest-post-report-inventory-align.json')


def call(module_name: str, arg: Any = None) -> dict[str, Any]:
    try:
        module = __import__(module_name, fromlist=['main'])
        fn = getattr(module, 'main', None)
        if not callable(fn):
            return {'module': module_name, 'status': 'missing_main'}
        if arg is None:
            code = fn()
        else:
            try:
                code = fn(arg)
            except TypeError:
                code = fn()
        return {'module': module_name, 'status': 'ok', 'code': int(code or 0)}
    except Exception as exc:
        return {'module': module_name, 'status': 'error_ignored', 'error': f'{type(exc).__name__}: {exc}'}


def main() -> int:
    steps = [
        call('scripts.guard_day_inventory_no_shrink', ['repair']),
        call('scripts.expand_day_inventory_to_target'),
        call('scripts.deduplicate_day_inventory_semantic'),
        call('scripts.expand_day_inventory_to_target'),
        call('scripts.backfill_inventory_bookmaker_coverage'),
        call('scripts.bridge_runtime_context_coverage'),
        call('scripts.build_day_inventory_coverage_truth'),
        call('scripts.day_inventory_cumulative_coverage'),
    ]
    payload = {'status': 'ok', 'created_at_utc': datetime.now(timezone.utc).isoformat(), 'steps': steps}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
