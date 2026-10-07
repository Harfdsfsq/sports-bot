"""Refresh an incomplete inventory at most once per two hours."""
from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta

from app.services.daily_match_registry import parse_time, read_json

REVISION = 3


def refresh_due(payload, now):
    if not payload or payload.get('daily_inventory_revision') != REVISION:
        return True
    if len(payload.get('matches') or []) >= 300:
        return False
    observed = parse_time(payload.get('updated_at_utc') or payload.get('created_at_utc'))
    return observed is None or now - observed >= timedelta(hours=2)


if __name__ == '__main__':
    raise SystemExit(0 if refresh_due(read_json(sys.argv[1], {}), datetime.now(UTC)) else 1)
