from __future__ import annotations

"""Startup compatibility patches for odds-api.io provider.

The live API currently returns ``/odds/multi`` payloads in a bookmaker-object
shape, for example::

    {"bookmakers": {"Betfair Exchange": [{"name": "ML", "odds": [{...}]}]}}

The production parser was still geared toward a flat outcome-list shape, so
HTTP 200 responses with real odds were counted as ``offers_parsed = 0``. This
module keeps the existing helper binding fixes and installs a robust parser for
both payload styles.
"""

import re
from functools import wraps
from typing import Any, Callable

from app.schemas import Offer


HELPER_NAMES = (
    "_is_supported_market",
    "_family_for_market",
    "_line_from_value",
    "_map_h2h_selection",
    "_normalize_yes_no",
    "_normalize_double_chance_selection",
    "_normalize_team_total_selection",
    "_infer_team_total_side",
    "_canonical_bookmaker",
)

NON_FULL_TIME_RE = re.compile(
    r"\b(1h|2h|ht|half|1st\s*half|2nd\s*half|corner|corners|card|cards|booking|player|shot|shots|save|saves|offside|period|quarter|set|map)\b",
    re.IGNORECASE,
)


def _raw_class_attr(cls: type[Any], name: str) -> Any:
    try:
        value = cls.__dict__.get(name)
        if isinstance(value, staticmethod):
            return value.__func__
        if isinstance(value, classmethod):
            return value.__func__
        return value
    except Exception:
        return None


def _make_binding_safe(raw: Callable[..., Any]) -> Callable[..., Any]:
    @wraps(raw)
    def binding_safe(self: Any, *args: Any, **kwargs: Any) -> Any:
        try:
            return raw(*args, **kwargs)
        except TypeError as first_exc:
            try:
                return raw(self, *args, **kwargs)
            except TypeError:
                raise first_exc

    return binding_safe


def _to_float(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(str(value).replace(",", ".").strip())
    except Exception:
        return None


def _point_from_text(*values: Any) -> float | None:
    for value in values:
        if value in (None, ""):
            continue
        direct = _to_float(value)
        if direct is not None:
            return direct
        text = str(value)
        match = re.search(r"(-?\d+(?:[\.,]\d+)?)", text)
        if match:
            return _to_float(match.group(1))
    return None


def _canonical_book(provider: Any, value: str) -> str:
    try:
        return str(provider._canonical_bookmaker(value))
    except Exception:
        return str(value or "").strip()


def _market_family(market_name: Any) -> str | None:
    text = str(market_name or "").strip().lower()
    compact = re.sub(r"[^a-z0-9]+", "", text)
    if not text or NON_FULL_TIME_RE.search(text):
        return None
    if compact in {"ml", "moneyline", "matchwinner", "1x2", "winner", "fulltimewinner"} or text in {"ml", "1x2"}:
        return "h2h"
    if "draw no bet" in text or compact in {"dnb", "drawnobet"}:
        return "dnb"
    if "double chance" in text or compact in {"doublechance"}:
        return "doubleChance"
    if "both teams" in text or "btts" in compact:
        return "btts"
    if "team total" in text or "team totals" in text:
        return "teamTotals"
    if "spread" in text or "handicap" in text or "asian handicap" in text:
        return "spreads"
    if "total" in text or "goals" in text or compact in {"ou", "overunder"}:
        return "totals"
    return None


def _iter_bookmaker_markets(bookmakers: Any) -> list[tuple[str, list[dict[str, Any]]]]:
    out: list[tuple[str, list[dict[str, Any]]]] = []
    if isinstance(bookmakers, dict):
        for book, markets in bookmakers.items():
            if isinstance(markets, list):
                out.append((str(book), [m for m in markets if isinstance(m, dict)]))
            elif isinstance(markets, dict):
                values = markets.get("markets") or markets.get("odds") or markets.get("data") or []
                if isinstance(values, list):
                    out.append((str(book), [m for m in values if isinstance(m, dict)]))
    elif isinstance(bookmakers, list):
        for row in bookmakers:
            if not isinstance(row, dict):
                continue
            book = str(row.get("name") or row.get("bookmaker") or row.get("title") or row.get("key") or "").strip()
            markets = row.get("markets") or row.get("odds") or row.get("data") or []
            if book and isinstance(markets, list):
                out.append((book, [m for m in markets if isinstance(m, dict)]))
    return out


def _market_rows(market: dict[str, Any]) -> list[dict[str, Any]]:
    for key in ("odds", "outcomes", "prices", "selections", "values"):
        value = market.get(key)
        if isinstance(value, list):
            return [x for x in value if isinstance(x, dict)]
    return []


def _price(row: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in row and row.get(key) not in (None, ""):
            return row.get(key)
    return None


def _add_offer(
    offers: list[Offer],
    seen: set[tuple[Any, ...]],
    provider: Any,
    payload: dict[str, Any],
    bookmaker: str,
    family: str,
    selection: str,
    price_value: Any,
    point: Any = None,
    market_name: str = "",
    team_side: str | None = None,
) -> None:
    price = _to_float(price_value)
    if price is None or price <= 1.0:
        return
    point_value = _point_from_text(point)
    book = _canonical_book(provider, bookmaker)
    normalized_side = str(team_side or "").strip().lower() or None
    key = (book, family, selection, point_value, normalized_side, market_name)
    if key in seen:
        return
    seen.add(key)
    offers.append(
        Offer(
            source="odds_api_io",
            bookmaker=book,
            family=family,  # type: ignore[arg-type]
            selection=selection,
            price=price,
            point=point_value,
            team_side=normalized_side,
            market_name=market_name,
            market_key=family,
            source_event_id=str(payload.get("id") or "") or None,
            metadata={
                "odds_api_io": True,
                "odds_api_io_event_id": payload.get("id"),
                "raw_market_name": market_name,
            },
        )
    )


def _parse_payload_object(provider: Any, payload: dict[str, Any], match: Any) -> list[Offer]:
    offers: list[Offer] = []
    seen: set[tuple[Any, ...]] = set()
    bookmakers = payload.get("bookmakers")
    for bookmaker, markets in _iter_bookmaker_markets(bookmakers):
        for market in markets:
            market_name = str(market.get("name") or market.get("market") or market.get("marketName") or market.get("key") or "").strip()
            family = _market_family(market_name)
            if family is None:
                continue
            rows = _market_rows(market)
            for row in rows:
                hdp = row.get("hdp") if "hdp" in row else row.get("point") or row.get("line") or row.get("handicap")
                if family == "h2h":
                    _add_offer(offers, seen, provider, payload, bookmaker, "h2h", match.home_team, _price(row, "home", "1"), None, market_name, "home")
                    _add_offer(offers, seen, provider, payload, bookmaker, "h2h", "Draw", _price(row, "draw", "x", "X"), None, market_name, None)
                    _add_offer(offers, seen, provider, payload, bookmaker, "h2h", match.away_team, _price(row, "away", "2"), None, market_name, "away")
                elif family == "dnb":
                    _add_offer(offers, seen, provider, payload, bookmaker, "dnb", match.home_team, _price(row, "home", "1"), 0, market_name, "home")
                    _add_offer(offers, seen, provider, payload, bookmaker, "dnb", match.away_team, _price(row, "away", "2"), 0, market_name, "away")
                elif family == "doubleChance":
                    for key in ("1X", "12", "X2", "1x", "x2"):
                        if key in row:
                            _add_offer(offers, seen, provider, payload, bookmaker, "doubleChance", key.upper(), row.get(key), None, market_name)
                elif family == "spreads":
                    p = _point_from_text(hdp)
                    _add_offer(offers, seen, provider, payload, bookmaker, "spreads", match.home_team, _price(row, "home", "1"), p, market_name, "home")
                    _add_offer(offers, seen, provider, payload, bookmaker, "spreads", match.away_team, _price(row, "away", "2"), (-p if p is not None else None), market_name, "away")
                elif family in {"totals", "teamTotals"}:
                    for key, value in row.items():
                        low = str(key).strip().lower()
                        if low.startswith("over"):
                            point = _point_from_text(row.get("hdp"), row.get("point"), row.get("line"), key)
                            _add_offer(offers, seen, provider, payload, bookmaker, family, "Over", value, point, market_name)
                        elif low.startswith("under"):
                            point = _point_from_text(row.get("hdp"), row.get("point"), row.get("line"), key)
                            _add_offer(offers, seen, provider, payload, bookmaker, family, "Under", value, point, market_name)
                    # Outcome-list fallback: {name: "Over 2.5", value: 1.9}
                    name = str(row.get("name") or row.get("label") or row.get("selection") or "").strip()
                    if name.lower().startswith(("over", "under")):
                        selection = "Over" if name.lower().startswith("over") else "Under"
                        value = _price(row, "price", "odds", "decimal", "value")
                        _add_offer(offers, seen, provider, payload, bookmaker, family, selection, value, _point_from_text(hdp, name), market_name)
                elif family == "btts":
                    for key in ("yes", "Yes", "YES"):
                        if key in row:
                            _add_offer(offers, seen, provider, payload, bookmaker, "btts", "Yes", row.get(key), None, market_name)
                    for key in ("no", "No", "NO"):
                        if key in row:
                            _add_offer(offers, seen, provider, payload, bookmaker, "btts", "No", row.get(key), None, market_name)
                    name = str(row.get("name") or row.get("label") or row.get("selection") or "").strip().lower()
                    if name in {"yes", "no"}:
                        _add_offer(offers, seen, provider, payload, bookmaker, "btts", name.title(), _price(row, "price", "odds", "decimal", "value"), None, market_name)
    return offers


def _patch_payload_parser(cls: type[Any]) -> bool:
    if getattr(cls, "_harizon_bookmaker_object_parser_installed", False):
        return False
    original = getattr(cls, "_parse_event_odds", None)
    if not callable(original):
        return False

    def parse_event_odds_patched(self: Any, payload: dict[str, Any], match: Any) -> list[Offer]:
        parsed: list[Offer] = []
        try:
            parsed = original(self, payload, match)
        except Exception:
            parsed = []
        if parsed:
            return parsed
        if isinstance(payload, dict) and isinstance(payload.get("bookmakers"), (dict, list)):
            return _parse_payload_object(self, payload, match)
        return []

    cls._parse_event_odds = parse_event_odds_patched
    cls._harizon_bookmaker_object_parser_installed = True
    return True


def install() -> dict[str, str]:
    from app.providers import odds_api_io

    cls = getattr(odds_api_io, "OddsApiIoProvider", None)
    if cls is None:
        return {"status": "skipped", "reason": "provider_class_missing"}

    fixed: list[str] = []
    for name in HELPER_NAMES:
        raw = _raw_class_attr(cls, name)
        if not callable(raw):
            continue
        setattr(cls, name, _make_binding_safe(raw))
        fixed.append(name)

    parser_installed = _patch_payload_parser(cls)

    cls._harizon_startup_compat_installed = True
    cls._harizon_startup_compat_version = "binding-safe-v6-bookmaker-object-parser"
    cls._harizon_startup_compat_fixed = fixed
    return {
        "status": "installed",
        "version": "binding-safe-v6-bookmaker-object-parser",
        "fixed": ",".join(fixed),
        "bookmaker_object_parser": str(bool(parser_installed)).lower(),
    }
