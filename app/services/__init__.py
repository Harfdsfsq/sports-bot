from __future__ import annotations

"""Service package startup hooks.

These hooks run before sitecustomize imports app.services.telegram_runtime_safety.
They keep publication behavior aligned with the current policy:
- publish a valid selected pick unless it is a duplicate or fails quality gates;
- do not block Telegram solely because odds_sources_count == 1;
- allow Asian quarter total lines (.25/.75) only with enough bookmaker support.
"""

import os

__all__ = []

# Do not let older runtime fixups reintroduce a hard odds-source diversity gate.
os.environ.setdefault("CONTROLLED_FALLBACK_REQUIRE_ODDS_SOURCE_DIVERSITY", "false")
os.environ.setdefault("CONTROLLED_FALLBACK_MIN_ODDS_SOURCES", "1")
os.environ.setdefault("TELEGRAM_MIN_ODDS_SOURCES", "1")
os.environ.setdefault("TELEGRAM_SINGLE_SOURCE_MIN_BOOKS", "1")

# Quarter totals are valid Asian lines. Settlement already supports half_won and
# half_lost grading, so the candidate policy should gate them by bookmaker
# support instead of deleting them globally.
os.environ.setdefault("ENABLE_QUARTER_TOTAL_LINES", "true")
os.environ.setdefault("QUARTER_TOTAL_MIN_BOOKS", "2")

# Mark old Telegram market-structure guards as installed so sitecustomize skips
# the outdated single-source blocking layer. The newer telegram_runtime_safety
# module still normalizes text and records send success/failure.
try:
    from urllib import parse, request
    parse._harizon_stake_percent_patch = True
    request._harizon_total_price_guard_patch = True
except Exception:
    pass

try:
    import httpx
    httpx.AsyncClient._harizon_total_price_guard_patch = True
except Exception:
    pass

# Install candidate-level market policy. It gates .25/.75 total lines by real
# bookmaker support and keeps destructive legacy filters disabled.
try:
    from app.services import runtime_market_policy
    runtime_market_policy.install()
except Exception:
    pass
