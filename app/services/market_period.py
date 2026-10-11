"""Reject sub-period quotes before using the full-match football model."""
from __future__ import annotations

import re
from typing import Any

_PERIOD_FIELDS = ('period', 'market_period', 'marketPeriod', 'time_scope', 'scope')
_FULL_MATCH = {'ft', 'full time', 'full match', 'fulltime', 'fullmatch', 'match', 'regular time', 'regulation', '90', '90 minutes'}
_SUB_PERIOD = re.compile(
    r'\b(?:[12]\s*h|h\s*[12]|ht|[12](?:st|nd)?\s*half|half\s*[12]|first\s*half|second\s*half|'
    r'half\s*time|halftime|[1-4]\s*q|q\s*[1-4]|quarter|period|extra\s*time|overtime|et|penalties|'
    r'[12](?:st|nd)?\s*тайм|тайм\s*[12]|первый\s*тайм|второй\s*тайм)\b', re.I,
)


def is_full_match_market(row: Any) -> bool:
    """Legacy unqualified names mean full match; explicit unknown periods fail closed."""
    def get(key, default=None):
        return row.get(key, default) if isinstance(row, dict) else getattr(row, key, default)

    def normalized(value):
        return re.sub(r'[_-]+', ' ', str(value or '').lower()).strip()

    labels = ' '.join(normalized(get(key)) for key in ('name', 'key', 'market', 'market_name', 'market_key', 'market_subtype'))
    if _SUB_PERIOD.search(labels):
        return False
    metadata = get('metadata') or {}
    for container in (row, metadata):
        for key in _PERIOD_FIELDS:
            value = container.get(key) if isinstance(container, dict) else getattr(container, key, None)
            if value not in (None, '') and normalized(value) not in _FULL_MATCH:
                return False
    return True
