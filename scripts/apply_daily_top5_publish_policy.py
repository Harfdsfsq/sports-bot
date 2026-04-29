from __future__ import annotations

"""Compatibility wrapper for the unified daily best-5 governor.

Historically this script handled top5 publishing and daily limits. To unify logic,
all thresholds and pacing are now handled in scripts/apply_daily_best5_governor.py.
"""

from scripts.apply_daily_best5_governor import main


if __name__ == "__main__":
    raise SystemExit(main())
