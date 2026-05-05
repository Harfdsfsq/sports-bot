from __future__ import annotations

"""Compatibility guard for OddsApiIoProvider._is_supported_market.

A previous runtime guard can leave _is_supported_market with a descriptor shape
that is incompatible with the provider call site.  The result is:

    TypeError: OddsApiIoProvider._is_supported_market() takes 1 positional argument but 2 were given

When that happens the provider fetches bootstrap matches but returns zero offers.
This guard installs a tolerant method that accepts both static-style and
instance-style calls and keeps the non-full-time market protection.
"""

import re
from typing import Any

PATCH_MARKER = "_harizon_odds_api_io_market_signature_guard_v1"

NON_FULL_TIME_MARKET_RE = re.compile(
    r"\b("
    r"ht|1h|2h|1st\s*half|2nd\s*half|first\s*half|second\s*half|half\s*time|"
    r"corners?|cards?|bookings?|offsides?|throw\s*ins?|shots?|saves?|player|"
    r"penalt(?:y|ies)|free\s*kicks?|goal\s*kicks?|period|quarter|set|map|"
    r"перв(?:ый|ом)\s*тайм|втор(?:ой|ом)\s*тайм|тайм|углов|карточ"
    r")\b",
    re.IGNORECASE,
)

CORE_MARKET_TOKENS = (
    "h2h",
    "1x2",
    "match_winner",
    "moneyline",
    "winner",
    "total",
    "totals",
    "over_under",
    "spread",
    "spreads",
    "handicap",
    "asian_handicap",
)


def _market_text(value: Any) -> str:
    if isinstance(value, dict):
        fields = (
            "key",
            "market_key",
            "market",
            "name",
            "label",
            "title",
            "marketName",
            "market_name",
        )
        return " ".join(str(value.get(field) or "") for field in fields).strip()
    return str(value or "").strip()


def _is_non_full_time(value: Any) -> bool:
    text = _market_text(value)
    return bool(text and NON_FULL_TIME_MARKET_RE.search(text))


def _looks_core_market(value: Any) -> bool:
    text = _market_text(value).lower().replace("-", "_").replace(" ", "_")
    if not text:
        return False
    return any(token in text for token in CORE_MARKET_TOKENS)


def install() -> bool:
    try:
        from app.providers import odds_api_io as module
    except Exception:
        return False
    cls = getattr(module, "OddsApiIoProvider", None)
    if cls is None or getattr(cls, PATCH_MARKER, False):
        return False

    previous = getattr(cls, "_is_supported_market", None)

    def supported_market_compat(self_or_market: Any = None, market_key: Any = None, *args: Any, **kwargs: Any) -> bool:
        market = market_key if market_key is not None else self_or_market
        if "market_key" in kwargs and kwargs.get("market_key") is not None:
            market = kwargs.get("market_key")
        if args and market is None:
            market = args[0]
        if _is_non_full_time(market):
            return False

        # Prefer the original implementation when it can be called safely.
        if callable(previous):
            for call_args in ((market,), (self_or_market, market)):
                try:
                    return bool(previous(*call_args))
                except TypeError:
                    continue
                except Exception:
                    break

        # Safe fallback: allow only core full-time betting families.
        return _looks_core_market(market)

    cls._is_supported_market = supported_market_compat
    setattr(cls, PATCH_MARKER, True)
    return True
