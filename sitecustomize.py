from __future__ import annotations

"""Repository-local Python startup shim.

Keep this module intentionally boring: it only makes repository scripts
importable. Runtime preparation lives in explicit entrypoints such as
``app.services.runtime_preflight``.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SCRIPTS = ROOT / "scripts"

for path in (ROOT, SCRIPTS):
    text = str(path)
    if text not in sys.path:
        sys.path.insert(0, text)
