from __future__ import annotations

"""Normalize bookmaker allow-lists for the normal run.

The bot now requests two odds-api.io accounts:
  account1: Bet365, Unibet
  account2: Betfair Exchange, Sbobet

The previous runtime still treated some account2 books as non-target books in
pre-candidate filtering.  That created many `non_target_bookmaker` rejections and
made the publish-books guard too strict even when exact offers existed.  This
guard sets all likely bookmaker allow-list environment aliases before app.config
is loaded.  It does not lower price-integrity protections: suspicious Over 1.5
still needs the exact-line guard.
"""

import os

BOOKMAKER_UNIVERSE = "Bet365,Unibet,Betfair Exchange,Sbobet"

ALIASES = {
    "BOOKMAKERS": BOOKMAKER_UNIVERSE,
    "BOOKMAKER_WHITELIST": BOOKMAKER_UNIVERSE,
    "ALLOWED_BOOKMAKERS": BOOKMAKER_UNIVERSE,
    "TARGET_BOOKMAKERS": BOOKMAKER_UNIVERSE,
    "PUBLISH_BOOKMAKERS": BOOKMAKER_UNIVERSE,
    "PUBLISH_TARGET_BOOKMAKERS": BOOKMAKER_UNIVERSE,
    "ODDS_TARGET_BOOKMAKERS": BOOKMAKER_UNIVERSE,
    "ODDS_API_IO_BOOKMAKERS": "Bet365,Unibet",
    "ODDS_API_IO_BOOKMAKERS_1": "Bet365,Unibet",
    "ODDS_API_IO_BOOKMAKERS_ACCOUNT1": "Bet365,Unibet",
    "ODDS_API_IO_BOOKMAKERS_2": "Betfair Exchange,Sbobet",
    "ODDS_API_IO_BOOKMAKERS_ACCOUNT2": "Betfair Exchange,Sbobet",
    # Keep normal publication from requiring a third bookmaker on every market.
    # The separate Over 1.5 suspicious-price guard still requires 3 exact books.
    "PUBLISH_MIN_BOOKS": "2",
    "PUBLISH_MIN_EXACT_BOOKS": "2",
    "MIN_PUBLISH_BOOKS": "2",
    "MIN_EXACT_LINE_BOOKS": "2",
    "MATCH_TOTAL_OVER15_MIN_EXACT_BOOKS": "3",
    "MATCH_TOTAL_OVER15_MAX_REASONABLE_ODDS": "1.65",
}


def install() -> bool:
    for key, value in ALIASES.items():
        os.environ.setdefault(key, value)
    return True
