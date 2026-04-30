from __future__ import annotations

import runpy
from pathlib import Path


def main() -> int:
    path = Path('scripts/apply_odds_api_io_bookmaker_patch.py')
    if not path.exists():
        raise SystemExit(f'missing odds routing patch: {path}')
    runpy.run_path(str(path), run_name='__main__')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
