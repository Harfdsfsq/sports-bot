from __future__ import annotations

"""Runtime hardening for the SportLogic provider.

The provider must survive slightly different SportLogic JSON envelopes and odds
row shapes.  This module is intentionally self-contained so it can be installed
from sitecustomize/runtime hooks without making the main runner brittle.
"""

import os
import re
from pathlib import Path
from typing import Any

PATCH_MARKER = "_harizon_sportlogic_hardening_v4"
FILE_HOOK_MARKER = "harizon_sportlogic_hardening_file_hook_v4"


def _float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        try:
            return float(value)
        except Exception:
            return None
    text = str(value).strip().replace(",", ".")
    if not text or text.lower() in {"none", "null", "n/a", "na", "-", "--"}:
        return None
    match = re.search(r"[-+]?\d+(?:\.\d+)?", text)
    if not match:
        return None
    try:
        return float(match.group(0))
    except Exception:
        return None


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return ""
    return str(value).strip()


def _first_text(*values: Any) -> str:
    for value in values:
        text = _text(value)
        if text:
            return text
    return ""


def _dig(payload: Any, *path: str) -> Any:
    value = payload
    for key in path:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def _is_price_like(value: Any) -> bool:
    number = _float(value)
    return number is not None and number > 1.0


def _is_odds_row(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    keys = {str(key).lower() for key in payload.keys()}
    price_keys = {
        "price",
        "decimal_odds",
        "value",
        "odd",
        "odds",
        "decimal",
        "option_value",
        "home_odds",
        "draw_odds",
        "away_odds",
        "odd_1",
        "odd_x",
        "odd_2",
        "btts_yes",
        "btts_no",
    }
    shape_keys = {
        "market",
        "market_name",
        "market_key",
        "market_id",
        "selection",
        "outcome",
        "option",
        "option_name",
        "label",
        "bookmaker",
        "bookmaker_name",
        "sportsbook",
        "provider",
        "book",
        "bookmakers",
        "markets",
        "bets",
        "market_odds",
        "bookmaker_markets",
    }
    if keys & price_keys:
        return True
    return bool(keys & shape_keys and ({"outcomes", "values", "selections", "options"} & keys))


def _extract_rows_recursive(payload: Any, depth: int = 0) -> list[dict[str, Any]]:
    if depth > 8:
        return []
    if isinstance(payload, list):
        rows: list[dict[str, Any]] = []
        for item in payload:
            if isinstance(item, dict):
                if _is_odds_row(item):
                    rows.append(item)
                else:
                    nested = _extract_rows_recursive(item, depth + 1)
                    rows.extend(nested or [item])
        return rows
    if not isinstance(payload, dict):
        return []
    if _is_odds_row(payload):
        return [payload]
    for key in (
        "data",
        "response",
        "results",
        "odds",
        "markets",
        "bookmakers",
        "items",
        "fixtures",
        "matches",
        "events",
    ):
        if key not in payload:
            continue
        value = payload.get(key)
        rows = _extract_rows_recursive(value, depth + 1)
        if rows:
            return rows
        if isinstance(value, dict) and _is_odds_row(value):
            return [value]
    rows: list[dict[str, Any]] = []
    for value in payload.values():
        rows.extend(_extract_rows_recursive(value, depth + 1))
        if rows:
            return rows
    return []


def extract_odds_rows(payload: Any) -> list[dict[str, Any]]:
    return _extract_rows_recursive(payload)


def _market_descriptor(row: dict[str, Any]) -> tuple[str, str]:
    market = row.get("market")
    if isinstance(market, dict):
        key = _first_text(market.get("key"), market.get("slug"), market.get("code"), market.get("id"), row.get("market_key"), row.get("market_id"))
        name = _first_text(market.get("name"), market.get("title"), market.get("label"), key, row.get("market_name"))
        return key, name
    key = _first_text(row.get("market_key"), row.get("key"), row.get("market_id"), market, row.get("type"))
    name = _first_text(row.get("market_name"), row.get("name") if not _is_price_like(row.get("name")) else "", key)
    return key, name


def _bookmaker_name(row: dict[str, Any], fallback: str = "SportLogic") -> str:
    book = row.get("bookmaker")
    if isinstance(book, dict):
        return _first_text(book.get("name"), book.get("title"), book.get("key"), book.get("slug"), book.get("id"), fallback)
    return _first_text(row.get("bookmaker_name"), book, row.get("sportsbook"), row.get("provider"), row.get("book"), fallback)


def _selection_name(row: dict[str, Any]) -> str:
    outcome = row.get("outcome")
    if isinstance(outcome, dict):
        return _first_text(outcome.get("name"), outcome.get("label"), outcome.get("selection"), outcome.get("option_name"), outcome.get("team"))
    return _first_text(row.get("option_name"), row.get("selection"), outcome, row.get("option"), row.get("label"), row.get("team"), row.get("name"))


def _price_value(row: dict[str, Any]) -> Any:
    outcome = row.get("outcome")
    if isinstance(outcome, dict):
        for key in ("price", "decimal_odds", "odds", "odd", "value", "decimal", "option_value"):
            value = outcome.get(key)
            if _is_price_like(value):
                return value
    odds_value = row.get("odds")
    if not isinstance(odds_value, (dict, list)) and _is_price_like(odds_value):
        return odds_value
    for key in ("price", "decimal_odds", "value", "odd", "decimal", "option_price"):
        value = row.get(key)
        if _is_price_like(value):
            return value
    return None


def _line_value(row: dict[str, Any]) -> float | None:
    outcome = row.get("outcome")
    if isinstance(outcome, dict):
        for key in ("point", "line", "total", "handicap", "points", "option_value"):
            value = outcome.get(key)
            number = _float(value)
            if number is not None and not _is_price_like(value):
                return number
    for key in ("point", "line", "total", "handicap", "points", "option_value"):
        value = row.get(key)
        number = _float(value)
        if number is not None and key != "option_value":
            return number
        if number is not None and not _is_price_like(value):
            return number
    return None


def _family_from_market(*parts: str) -> str:
    text = " ".join(str(part or "") for part in parts).lower().replace("-", "_").replace(" ", "_")
    if any(token in text for token in ("both_teams_to_score", "btts", "bothteams")):
        return "btts"
    if any(token in text for token in ("goals_over_under", "total_goals", "over_under", "totals", "total", "goals_o_u")):
        return "totals"
    if any(token in text for token in ("asian_handicap", "handicap", "spread", "spreads")):
        return "spreads"
    if any(token in text for token in ("match_winner", "full_time_result", "1x2", "winner", "moneyline", "h2h", "three_way")):
        return "h2h"
    return ""


def _line_from_text(*parts: str) -> float | None:
    text = " ".join(str(part or "") for part in parts)
    match = re.search(r"[-+]?\d+(?:[.,]\d+)?", text)
    if not match:
        return None
    return _float(match.group(0))


def _normalise_flat_row(row: dict[str, Any], inherited_book: str = "", inherited_market_key: str = "", inherited_market_name: str = "") -> dict[str, Any] | None:
    if not isinstance(row, dict) or bool(row.get("is_suspended")) or bool(row.get("suspended")):
        return None
    out = dict(row)
    market_key, market_name = _market_descriptor(row)
    if inherited_market_key and not market_key:
        market_key = inherited_market_key
    if inherited_market_name and not market_name:
        market_name = inherited_market_name
    family = _family_from_market(market_key, market_name)
    selection = _selection_name(row)
    price = _price_value(row)
    line = _line_value(row)
    book = _bookmaker_name(row, inherited_book or "SportLogic")

    out["bookmaker_name"] = book
    if family:
        out["market_name"] = family
        out["market_key"] = family
    elif market_name or market_key:
        out["market_name"] = market_name or market_key
    if selection:
        out["selection"] = selection
        out["option_name"] = selection
    if price is not None:
        out["price"] = price
        out["decimal_odds"] = price
    if line is not None:
        if family == "totals":
            out["total"] = line
        elif family == "spreads":
            out["handicap"] = line
        out["point"] = line
        out["line"] = line
    return out


def _market_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for key in ("markets", "odds", "bets", "bookmaker_markets", "market_odds"):
        value = payload.get(key)
        if isinstance(value, list):
            rows.extend([row for row in value if isinstance(row, dict)])
        elif isinstance(value, dict):
            for name, nested in value.items():
                if isinstance(nested, list):
                    rows.append({"market_key": name, "market_name": name, "outcomes": nested})
                elif isinstance(nested, dict):
                    merged = dict(nested)
                    merged.setdefault("market_key", name)
                    merged.setdefault("market_name", name)
                    rows.append(merged)
                elif _is_price_like(nested):
                    rows.append({"market_key": name, "market_name": name, "price": nested})
    return rows


def _outcome_rows(market: dict[str, Any]) -> list[dict[str, Any]]:
    outcomes = None
    for key in ("outcomes", "values", "selections", "options", "odds"):
        if key in market:
            outcomes = market.get(key)
            break
    if isinstance(outcomes, dict):
        return [
            {"selection": name, "price": value}
            if not isinstance(value, dict)
            else {"selection": name, **value}
            for name, value in outcomes.items()
        ]
    if isinstance(outcomes, list):
        return [row for row in outcomes if isinstance(row, dict)]
    return []


def expand_odds_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    expanded: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if bool(row.get("is_suspended")) or bool(row.get("suspended")):
            continue
        bookmakers = row.get("bookmakers")
        if isinstance(bookmakers, list):
            for bookmaker in [item for item in bookmakers if isinstance(item, dict)]:
                book = _bookmaker_name(bookmaker)
                markets = _market_rows(bookmaker)
                if not markets and _is_odds_row(bookmaker):
                    normalised = _normalise_flat_row(bookmaker, inherited_book=book)
                    if normalised:
                        expanded.append(normalised)
                    continue
                for market in markets:
                    market_key, market_name = _market_descriptor(market)
                    outcomes = _outcome_rows(market)
                    if not outcomes:
                        normalised = _normalise_flat_row(market, inherited_book=book, inherited_market_key=market_key, inherited_market_name=market_name)
                        if normalised:
                            expanded.append(normalised)
                        continue
                    for outcome in outcomes:
                        merged = dict(outcome)
                        merged.setdefault("bookmaker_name", book)
                        merged.setdefault("market_key", market_key)
                        merged.setdefault("market_name", market_name)
                        normalised = _normalise_flat_row(merged, inherited_book=book, inherited_market_key=market_key, inherited_market_name=market_name)
                        if normalised:
                            expanded.append(normalised)
            continue

        markets = _market_rows(row)
        if markets:
            book = _bookmaker_name(row)
            for market in markets:
                market_key, market_name = _market_descriptor(market)
                outcomes = _outcome_rows(market)
                if outcomes:
                    for outcome in outcomes:
                        merged = dict(outcome)
                        merged.setdefault("bookmaker_name", book)
                        merged.setdefault("market_key", market_key)
                        merged.setdefault("market_name", market_name)
                        normalised = _normalise_flat_row(merged, inherited_book=book, inherited_market_key=market_key, inherited_market_name=market_name)
                        if normalised:
                            expanded.append(normalised)
                else:
                    normalised = _normalise_flat_row(market, inherited_book=book, inherited_market_key=market_key, inherited_market_name=market_name)
                    if normalised:
                        expanded.append(normalised)
            continue

        normalised = _normalise_flat_row(row)
        if normalised:
            expanded.append(normalised)
    return expanded


def _configured_bookmakers(self: Any) -> set[str]:
    configured = getattr(getattr(self, "settings", None), "sportlogic_bookmakers", None)
    if configured is None:
        configured = os.getenv("SPORTLOGIC_BOOKMAKERS", "")
    if isinstance(configured, str):
        items = [item.strip() for item in configured.split(",") if item.strip()]
    else:
        items = [str(item).strip() for item in (configured or []) if str(item).strip()]
    canonical = getattr(self, "_canonical_bookmaker", None)
    result = {canonical(item) if callable(canonical) else item for item in items}
    result.discard("")
    return result


def _canonical_bookmaker(self: Any, name: str) -> str:
    canonical = getattr(self, "_canonical_bookmaker", None)
    if callable(canonical):
        try:
            return str(canonical(name))
        except Exception:
            pass
    try:
        from app.utils import normalize_bookmaker_name

        return str(normalize_bookmaker_name(name))
    except Exception:
        return str(name or "SportLogic")


def direct_parse_rows(self: Any, rows: list[dict[str, Any]], match: Any, event_id: str, stats: dict[str, Any] | None = None) -> list[Any]:
    try:
        from app.schemas import Offer
    except Exception:
        return []

    allowed = _configured_bookmakers(self)
    offers: list[Any] = []
    seen: set[tuple[str, str, str, float | None]] = set()

    def reject(reason: str) -> None:
        if stats is None:
            return
        reasons = stats.setdefault("sportlogic_hardening_reject_reasons", {})
        if isinstance(reasons, dict):
            reasons[reason] = int(reasons.get(reason) or 0) + 1

    def add(book: str, family: str, selection: str, price: Any, point: float | None = None, team_side: str | None = None, market_name: str = "") -> None:
        odds = _float(price)
        if odds is None or odds <= 1.0:
            reject("missing_or_invalid_price")
            return
        bookmaker = _canonical_bookmaker(self, book or "SportLogic")
        if allowed and bookmaker not in allowed:
            reject("bookmaker_not_allowed")
            return
        key = (bookmaker, family, selection, point)
        if key in seen:
            reject("duplicate_offer")
            return
        seen.add(key)
        offers.append(
            Offer(
                source="sportlogic",
                bookmaker=bookmaker,
                family=family,  # type: ignore[arg-type]
                selection=selection,
                price=float(odds),
                point=point,
                team_side=team_side,
                market_name=market_name or family,
                market_key=family,
                source_event_id=str(event_id or ""),
                metadata={"sportlogic_event_id": str(event_id or ""), "parser": "sportlogic_hardening_v4"},
            )
        )

    home = _text(getattr(match, "home_team", ""))
    away = _text(getattr(match, "away_team", ""))
    for row in expand_odds_rows(rows):
        book = _bookmaker_name(row)
        market_key, market_name = _market_descriptor(row)
        family = _family_from_market(market_key, market_name)
        selection = _selection_name(row)
        selection_low = selection.lower().strip()
        price = _price_value(row)
        point = _line_value(row)

        add(book, "h2h", home, row.get("home") or row.get("home_odds") or row.get("odd_1"), team_side="home", market_name="h2h")
        add(book, "h2h", "Draw", row.get("draw") or row.get("draw_odds") or row.get("odd_x"), market_name="h2h")
        add(book, "h2h", away, row.get("away") or row.get("away_odds") or row.get("odd_2"), team_side="away", market_name="h2h")
        add(book, "btts", "Yes", row.get("btts_yes") or row.get("both_teams_to_score_yes"), market_name="btts")
        add(book, "btts", "No", row.get("btts_no") or row.get("both_teams_to_score_no"), market_name="btts")

        if not family:
            family = _family_from_market(selection)
        if family == "h2h":
            if selection_low in {"home", "1", "home_team"} or selection == home:
                add(book, "h2h", home, price, team_side="home", market_name=market_name or "h2h")
            elif selection_low in {"draw", "x", "tie"}:
                add(book, "h2h", "Draw", price, market_name=market_name or "h2h")
            elif selection_low in {"away", "2", "away_team"} or selection == away:
                add(book, "h2h", away, price, team_side="away", market_name=market_name or "h2h")
            elif selection:
                side = "home" if home and home.lower() in selection_low else "away" if away and away.lower() in selection_low else None
                add(book, "h2h", selection, price, team_side=side, market_name=market_name or "h2h")
        elif family == "totals":
            if point is None:
                point = _line_from_text(selection, market_name, market_key)
            if "under" in selection_low or selection_low.startswith("u"):
                add(book, "totals", "Under", price, point, market_name=market_name or "totals")
            elif "over" in selection_low or selection_low.startswith("o"):
                add(book, "totals", "Over", price, point, market_name=market_name or "totals")
        elif family == "btts":
            if "yes" in selection_low:
                add(book, "btts", "Yes", price, market_name=market_name or "btts")
            elif "no" in selection_low:
                add(book, "btts", "No", price, market_name=market_name or "btts")
        elif family == "spreads":
            if selection_low in {"home", "1", "home_team"} or selection == home:
                add(book, "spreads", home, price, point, "home", market_name=market_name or "spreads")
            elif selection_low in {"away", "2", "away_team"} or selection == away:
                add(book, "spreads", away, price, point, "away", market_name=market_name or "spreads")

        for key, value in row.items():
            low = str(key).lower()
            total_match = re.search(r"(over|under|[ou])[_\s-]*(\d+(?:\.\d+)?)", low)
            if total_match:
                side = total_match.group(1)
                add(book, "totals", "Over" if side in {"over", "o"} else "Under", value, _float(total_match.group(2)), market_name="totals")
    return offers


def _looks_like_sportlogic_odds_row(payload: dict[str, Any]) -> bool:
    keys = {str(key).lower() for key in payload.keys()}
    return bool(keys & {
        "market", "market_id", "market_name", "market_key", "option_name",
        "option_value", "outcome", "selection", "bookmaker", "bookmaker_id",
        "odds", "decimal_odds", "price", "is_suspended",
    })


def _find_event_id(payload: Any) -> str:
    if isinstance(payload, dict):
        for key in ("game_id", "gameId", "fixture_id", "fixtureId", "event_id", "eventId", "match_id", "matchId"):
            value = payload.get(key)
            if value not in (None, ""):
                return str(value)
        for path in (("game", "id"), ("fixture", "id"), ("event", "id"), ("match", "id")):
            value = _dig(payload, *path)
            if value not in (None, ""):
                return str(value)
        if not _looks_like_sportlogic_odds_row(payload):
            value = payload.get("id")
            if value not in (None, ""):
                return str(value)
    return ""


def install() -> bool:
    try:
        from app.providers import sportlogic_provider as module
    except Exception:
        return False
    cls = getattr(module, "SportLogicProvider", None)
    if cls is None or getattr(cls, PATCH_MARKER, False):
        return False

    original_parse = getattr(cls, "_parse_odds", None)
    original_event_id = getattr(cls, "_event_id", None)

    if callable(original_parse):
        def parse_odds_patched(self: Any, rows: list[dict[str, Any]], match: Any, event_id: str, stats: dict[str, Any] | None = None) -> list[Any]:
            source_rows = rows if isinstance(rows, list) else extract_odds_rows(rows)
            expanded = expand_odds_rows([row for row in source_rows if isinstance(row, dict)])
            if stats is not None:
                stats["sportlogic_hardening_rows_in"] = int(stats.get("sportlogic_hardening_rows_in") or 0) + len(source_rows)
                stats["sportlogic_hardening_rows_expanded"] = int(stats.get("sportlogic_hardening_rows_expanded") or 0) + len(expanded)
            try:
                parsed = original_parse(self, expanded or source_rows, match, event_id, stats)
            except Exception as exc:
                parsed = []
                if stats is not None:
                    stats.setdefault("sportlogic_hardening_errors", []).append(f"original_parse:{type(exc).__name__}:{exc}")
            if parsed:
                return parsed
            fallback = direct_parse_rows(self, expanded or source_rows, match, event_id, stats)
            if fallback and stats is not None:
                stats["sportlogic_hardening_direct_offers"] = int(stats.get("sportlogic_hardening_direct_offers") or 0) + len(fallback)
            return fallback

        cls._parse_odds = parse_odds_patched

    async def fetch_odds_payload_patched(self: Any, client: Any, event_id: str, stats: dict[str, Any], preview: dict[str, Any]) -> Any | None:
        event_id = str(event_id or "").strip()
        endpoints = [
            (f"/games/{event_id}/odds", {}),
            ("/odds", {"game_id": event_id}),
            ("/odds", {"fixture_id": event_id}),
            ("/odds", {"event_id": event_id}),
            ("/games/odds", {"game_id": event_id}),
        ]
        seen: set[tuple[str, tuple[tuple[str, str], ...]]] = set()
        for path, params in endpoints:
            key = (path, tuple(sorted((str(k), str(v)) for k, v in params.items())))
            if key in seen:
                continue
            seen.add(key)
            budget_left = getattr(self, "_budget_left", None)
            if callable(budget_left) and not budget_left():
                stats["budget_exhausted"] = True
                return None
            stats["odds_requests"] = int(stats.get("odds_requests") or 0) + 1
            get_json = getattr(self, "_get_json", None)
            if not callable(get_json):
                return None
            payload = await get_json(client, path, params, stats, preview)
            rows = extract_odds_rows(payload)
            if rows:
                stats["odds_endpoint_used"] = path
                stats["odds_endpoint_params_used"] = dict(params)
                return payload
        return None

    cls._extract_odds_rows = staticmethod(extract_odds_rows)
    cls._fetch_odds_payload = fetch_odds_payload_patched

    if callable(original_event_id):
        def event_id_patched(self: Any, row: dict[str, Any]) -> str:
            # Prefer game_id-aware extraction before the original provider method;
            # the original used `id` first and could turn an odds-row id into
            # `/games/{odds_row_id}`, causing 404s and quota waste.
            value = _find_event_id(row)
            if value not in (None, ""):
                return str(value)
            try:
                value = original_event_id(self, row)
                if value not in (None, ""):
                    return str(value)
            except Exception:
                pass
            return ""

        cls._event_id = event_id_patched
        cls._game_id = staticmethod(_find_event_id)

    setattr(cls, PATCH_MARKER, True)
    return True


def patch_provider_file(root: str | Path | None = None) -> bool:
    base = Path(root) if root is not None else Path(__file__).resolve().parents[2]
    path = base / "app" / "providers" / "sportlogic_provider.py"
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return False
    if FILE_HOOK_MARKER in text:
        return False
    hook = f'''

# {FILE_HOOK_MARKER}: keep SportLogic parser/endpoints robust at runtime.
try:
    from app.providers import sportlogic_hardening as _harizon_sportlogic_hardening
    _harizon_sportlogic_hardening.install()
except Exception:
    pass
'''
    try:
        path.write_text(text.rstrip() + hook + "\n", encoding="utf-8")
        return True
    except Exception:
        return False
