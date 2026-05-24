"""Minimal startup shim for `python scripts/*.py`.

The previous version installed several legacy patchers on every script import and
some of them rewrote repository files. Standalone scripts now opt into their own
guards explicitly; this file only keeps the repository root importable.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
