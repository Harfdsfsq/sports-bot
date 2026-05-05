from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

if not str(os.getenv("HIGHLIGHTLY_BASE_URL") or "").strip():
    os.environ["HIGHLIGHTLY_BASE_URL"] = "https://soccer.highlightly.net"
if not str(os.getenv("HIGHLIGHTLY_SMOKE_PATH") or "").strip():
    os.environ["HIGHLIGHTLY_SMOKE_PATH"] = "/leagues"

from scripts.provider_smoke_all import main


if __name__ == "__main__":
    raise SystemExit(main())
