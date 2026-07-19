"""Choose the replayed runtime artifact during the workflow's rebase.

Generated TXT/CSV reports are snapshots rather than cumulative state. During
``git pull --rebase`` Git passes the commit being replayed as ``%B``; copying it to
``%A`` avoids conflict markers and preserves the current run's report.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    if len(args) < 3:
        print("usage: merge_runtime_latest.py BASE CURRENT OTHER [MARKER_SIZE] [PATH]", file=sys.stderr)
        return 2
    current = Path(args[1])
    other = Path(args[2])
    if other.exists():
        shutil.copyfile(other, current)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
