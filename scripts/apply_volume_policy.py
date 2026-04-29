from __future__ import annotations

"""Compatibility wrapper for the unified daily best-5 governor.

Historically this script owned daily hard/soft volume caps. That created conflicts
with the top5 publishing policy and could disable fallback evaluation entirely.
The single source of truth is now scripts/apply_daily_best5_governor.py.
"""

from scripts.apply_daily_best5_governor import main


if __name__ == "__main__":
    raise SystemExit(main())
