from __future__ import annotations

"""Install the strict 2+/2+ runtime source contract for controlled fallback.

The user-facing HARIZON contract is now: no Telegram publication unless the
candidate has at least two independent line/odds sources, two bookmaker/price
confirmations and two context/confirmation sources.  The older filename is kept
for compatibility with the guarded fallback wrapper that already imports it.
"""

import os
from typing import Any


VALUES = {
    'PUBLISH_MIN_ODDS_SOURCES': '2',
    'PUBLISH_MIN_CONTEXT_SOURCES': '2',
    'PUBLISH_TIER_A_MIN_ODDS_SOURCES': '2',
    'PUBLISH_TIER_A_MIN_BOOKS': '2',
    'PUBLISH_TIER_A_MIN_CONTEXT_SOURCES': '2',
    'PUBLISH_TIER_B_MIN_ODDS_SOURCES': '2',
    'PUBLISH_TIER_B_MIN_BOOKS': '2',
    'PUBLISH_TIER_B_MIN_CONTEXT_SOURCES': '2',
    'CONTROLLED_FALLBACK_MIN_ODDS_SOURCES': '2',
    'CONTROLLED_FALLBACK_MIN_CONTEXT_SOURCES': '2',
    'CONTROLLED_FALLBACK_MIN_CONFIRMATION_SOURCES': '2',
    'CONTROLLED_FALLBACK_TIER_A_REQUIRE_2_ODDS_SOURCES': 'true',
    'CONTROLLED_FALLBACK_TIER_A_MIN_ODDS_SOURCES': '2',
    'CONTROLLED_FALLBACK_TIER_A_MIN_BOOKS': '2',
    'CONTROLLED_FALLBACK_TIER_A_MIN_CONTEXT_SOURCES': '2',
    'CONTROLLED_FALLBACK_TIER_A_MIN_CONFIRMATION_SOURCES': '2',
    'CONTROLLED_FALLBACK_TIER_A_LINE_CONFIRMATION_MODE': 'independent_odds_source_and_bookmaker',
    'CONTROLLED_FALLBACK_TIER_B_MIN_ODDS_SOURCES': '2',
    'CONTROLLED_FALLBACK_TIER_B_MIN_BOOKS': '2',
    'CONTROLLED_FALLBACK_TIER_B_MIN_CONTEXT_SOURCES': '2',
    'CONTROLLED_FALLBACK_TIER_B_MIN_CONFIRMATION_SOURCES': '2',
    'CONTROLLED_FALLBACK_REQUIRE_2_ODDS_SOURCES_FOR_TELEGRAM': 'true',
    'CONTROLLED_FALLBACK_REQUIRE_2_CONTEXT_SOURCES_FOR_TELEGRAM': 'true',
}


def install(_base: Any = None) -> None:
    os.environ.update(VALUES)
    path = os.getenv('GITHUB_ENV')
    if path:
        with open(path, 'a', encoding='utf-8') as fh:
            for key, value in sorted(VALUES.items()):
                fh.write(f'{key}={value}\n')
