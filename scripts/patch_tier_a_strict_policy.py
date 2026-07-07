from __future__ import annotations

import os
from typing import Any


VALUES = {
    'CONTROLLED_FALLBACK_TIER_A_REQUIRE_2_ODDS_SOURCES': 'true',
    'CONTROLLED_FALLBACK_TIER_A_MIN_ODDS_SOURCES': '2',
    'CONTROLLED_FALLBACK_TIER_A_MIN_BOOKS': '2',
    'CONTROLLED_FALLBACK_TIER_A_MIN_CONTEXT_SOURCES': '2',
    'CONTROLLED_FALLBACK_TIER_A_MIN_CONFIRMATION_SOURCES': '2',
    'CONTROLLED_FALLBACK_TIER_A_LINE_CONFIRMATION_MODE': 'independent_odds_source_and_bookmaker',
}


def install(_base: Any = None) -> None:
    os.environ.update(VALUES)
    path = os.getenv('GITHUB_ENV')
    if path:
        with open(path, 'a', encoding='utf-8') as fh:
            for key, value in sorted(VALUES.items()):
                fh.write(f'{key}={value}\n')
