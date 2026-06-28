from __future__ import annotations

"""Path-safe wrapper for Actions artifact publication backfill.

The original script imports modules from the repository package while it is
usually launched as `python scripts/...`.  In that mode Python puts `scripts/` on
sys.path, not the repository root.  This wrapper adds the root first, then runs
the original entrypoint unchanged.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import backfill_publication_ledger_from_actions


def main() -> int:
    return int(backfill_publication_ledger_from_actions.main() or 0)


if __name__ == "__main__":
    raise SystemExit(main())
