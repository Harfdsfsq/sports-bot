from __future__ import annotations

import os

BOOKMAKER_UNIVERSE = (
    "Bet365,Unibet,William Hill,Bwin,Betfair Exchange,Sbobet"
)

ALIASES = {
    "BOOKMAKERS": BOOKMAKER_UNIVERSE,
    "BOOKMAKER_WHITELIST": BOOKMAKER_UNIVERSE,
    "ALLOWED_BOOKMAKERS": BOOKMAKER_UNIVERSE,
    "TARGET_BOOKMAKERS": BOOKMAKER_UNIVERSE,
    "CONSENSUS_BOOKMAKERS": BOOKMAKER_UNIVERSE,
    "PUBLISH_BOOKMAKERS": BOOKMAKER_UNIVERSE,
    "PUBLISH_TARGET_BOOKMAKERS": BOOKMAKER_UNIVERSE,
    "ODDS_TARGET_BOOKMAKERS": BOOKMAKER_UNIVERSE,
    "ODDS_API_IO_BOOKMAKERS": "Bet365,Unibet",
    "ODDS_API_IO_BOOKMAKERS_1": "Bet365,Unibet",
    "ODDS_API_IO_BOOKMAKERS_ACCOUNT1": "Bet365,Unibet",
    "ODDS_API_IO_BOOKMAKERS_2": "William Hill,Bwin",
    "ODDS_API_IO_BOOKMAKERS_ACCOUNT2": "William Hill,Bwin",
    # Actual Settings aliases.
    "MIN_BOOKS_PUBLISH": "2",
    "MIN_SOURCES_PUBLISH": "1",
    "MIN_BOOKS_FOR_CONSENSUS": "2",
    "STRONG_MARKET_MIN_BOOKS": "2",
    # Backward-compatible aliases used by older runtime guards.
    "PUBLISH_MIN_BOOKS": "1",
    "PUBLISH_MIN_EXACT_BOOKS": "1",
    "MIN_PUBLISH_BOOKS": "1",
    "MIN_EXACT_LINE_BOOKS": "1",
    # Keep the strict suspicious-price protection for low totals.
    "MATCH_TOTAL_OVER15_MIN_EXACT_BOOKS": "3",
    "MATCH_TOTAL_OVER15_MAX_REASONABLE_ODDS": "1.65",
}


def install() -> bool:
    for key, value in ALIASES.items():
        os.environ.setdefault(key, value)
    return True
