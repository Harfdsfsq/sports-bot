from __future__ import annotations

import re
from typing import Any

from app.schemas import Offer

PATCH_MARKER = "_harizon_odds_api_io_h2h_parser_guard_v1"
SKIP_RE = re.compile(r"\b(ht|1h|2h|half|corner|card|player|shot|offside)\b", re.I)
H2H_RE = re.compile(r"^(ml|match\s*odds|match\s*winner|1x2|full\s*time\s*result|result)$", re.I)


def _num(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(str(value).replace(",", "."))
    except Exception:
        return None


def install() -> bool:
    try:
        from app.providers import odds_api_io as module
    except Exception:
        return False
    cls = getattr(module, "OddsApiIoProvider", None)
    if cls is None or getattr(cls, PATCH_MARKER, False):
        return False
    original = getattr(cls, "_parse_event_odds", None)
    if not callable(original):
        return False

    def patched(self: Any, payload: dict[str, Any], match: Any) -> list[Offer]:
        offers = list(original(self, payload, match) or [])
        seen = {(o.bookmaker, o.family, o.selection, o.point, o.team_side) for o in offers}
        books = payload.get("bookmakers") if isinstance(payload, dict) else None
        if not isinstance(books, dict):
            return offers
        for book_name, markets in books.items():
            if not isinstance(markets, list):
                continue
            for market in markets:
                if not isinstance(market, dict):
                    continue
                name = str(market.get("name") or market.get("key") or "").strip()
                if not name or SKIP_RE.search(name) or not H2H_RE.search(name):
                    continue
                rows = market.get("odds") or market.get("outcomes") or []
                if isinstance(rows, dict):
                    rows = [rows]
                if not isinstance(rows, list):
                    continue
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    for selection, raw_price in ((match.home_team, row.get("home") or row.get("1")), ("Draw", row.get("draw") or row.get("x") or row.get("X")), (match.away_team, row.get("away") or row.get("2"))):
                        price = _num(raw_price)
                        if price is None or price <= 1.0 or price >= 100.0:
                            continue
                        try:
                            book = self._canonical_bookmaker(str(book_name))
                        except Exception:
                            book = str(book_name)
                        key = (book, "h2h", str(selection), None, None)
                        if key in seen:
                            continue
                        seen.add(key)
                        offers.append(Offer(source="odds_api_io", bookmaker=book, family="h2h", selection=str(selection), price=price, point=None, team_side=None, market_name=name, market_key="h2h", metadata={"odds_api_io": True, "parser_guard": "h2h"}))
        return offers

    cls._parse_event_odds = patched
    setattr(cls, PATCH_MARKER, True)
    return True
