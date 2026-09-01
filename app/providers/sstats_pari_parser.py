from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from app.schemas import Match, Offer
from app.utils import canonicalize_team_name, parse_datetime


_DISALLOWED_MARKET_TOKENS = (
    "first half",
    "1st half",
    "second half",
    "2nd half",
    "first period",
    "second period",
    "corners",
    "corner",
    "cards",
    "bookings",
    "shots",
    "offsides",
    "individual total",
    "team total",
    "индивидуальный тотал",
    "тотал команды",
    "углов",
    "карточ",
    "офсайд",
)


def unwrap(payload: Any) -> Any:
    current = payload
    for _ in range(4):
        if not isinstance(current, dict):
            break
        for key in ("data", "result", "response"):
            value = current.get(key)
            if isinstance(value, (dict, list)):
                current = value
                break
        else:
            break
    return current


def extract_list(payload: Any) -> list[dict[str, Any]]:
    data = unwrap(payload)
    if isinstance(data, list):
        return [row for row in data if isinstance(row, dict)]
    if isinstance(data, dict):
        for key in ("items", "matches", "rows", "result", "data"):
            value = data.get(key)
            if isinstance(value, list):
                return [row for row in value if isinstance(row, dict)]
    return []


def _flatten_market_rows(rows: list[Any]) -> list[dict[str, Any]]:
    flattened: list[dict[str, Any]] = []
    for item in rows:
        if not isinstance(item, dict):
            continue
        nested = item.get("odds") or item.get("outcomes")
        if isinstance(nested, list):
            market_name = str(item.get("marketName") or item.get("name") or "").strip()
            for outcome in nested:
                if not isinstance(outcome, dict):
                    continue
                row = dict(outcome)
                outcome_name = str(row.get("name") or row.get("label") or "").strip()
                if market_name and outcome_name and market_name.lower() not in outcome_name.lower():
                    row["name"] = f"{market_name}: {outcome_name}"
                flattened.append(row)
            continue
        flattened.append(item)
    return flattened


def extract_odds(payload: Any) -> list[dict[str, Any]]:
    data = unwrap(payload)
    if not isinstance(data, dict):
        return []
    for key in (
        "currentOdds",
        "current_odds",
        "prematchOdds",
        "preMatchOdds",
        "odds",
        "Odds",
        "coefficients",
        "outcomes",
        "markets",
    ):
        value = data.get(key)
        if isinstance(value, list):
            return _flatten_market_rows(value)
    match_info = data.get("matchInfo") or data.get("match_info")
    if isinstance(match_info, dict):
        for key in ("currentOdds", "current_odds", "odds", "outcomes", "markets"):
            value = match_info.get(key)
            if isinstance(value, list):
                return _flatten_market_rows(value)
    return []


def total_count(payload: Any, fallback: int) -> int:
    current = payload
    for _ in range(5):
        if not isinstance(current, dict):
            break
        for key in ("totalCount", "total_count", "total", "count"):
            if key in current:
                try:
                    return max(fallback, int(float(current[key])))
                except Exception:
                    pass
        current = next(
            (
                current[key]
                for key in ("data", "result", "response")
                if isinstance(current.get(key), dict)
            ),
            None,
        )
        if current is None:
            break
    return fallback


def event_id(row: dict[str, Any]) -> Any:
    return row.get("eventId") or row.get("event_id") or row.get("id")


def team_name(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("name") or value.get("title") or value.get("teamName") or "").strip()
    return str(value or "").strip()


def league_name(row: dict[str, Any]) -> str:
    value = row.get("tournament") or row.get("league") or row.get("competition")
    if isinstance(value, dict):
        return str(value.get("name") or value.get("title") or "").strip()
    return str(value or "").strip()


def parse_dt(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        dt = parse_datetime(value)
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def as_price(value: Any) -> float | None:
    try:
        return float(str(value).replace(",", "."))
    except Exception:
        return None


def _safe_full_time_market(text: str) -> bool:
    if any(token in text for token in _DISALLOWED_MARKET_TOKENS):
        return False
    period_markers = ("half", "period", "тайм", "четверть", "сет")
    if any(marker in text for marker in period_markers) and "full time" not in text:
        return False
    return True


def parse_market_name(
    name: str,
    match: Match,
) -> tuple[str, str, float | None, str | None, str] | None:
    text = re.sub(r"\s+", " ", name.strip()).lower().replace(",", ".")
    if not text or not _safe_full_time_market(text):
        return None
    numbers = [float(value) for value in re.findall(r"(?<!\d)[+-]?\d+(?:\.\d+)?", text)]
    point = numbers[-1] if numbers else None
    is_over = any(token in text for token in (" over", "over ", "больше", "тб", "б "))
    is_under = any(token in text for token in (" under", "under ", "меньше", "тм", "м "))
    if any(token in text for token in ("goals total", "goal total", "тотал", "total")) and (
        is_over or is_under
    ):
        if point is None or not 0 <= point <= 15:
            return None
        selection = "Over" if is_over else "Under"
        return "totals", selection, point, None, f"totals:{selection.lower()}:{point:g}"
    if any(token in text for token in ("asian handicap", "handicap", "spread", "фора")):
        if point is None or abs(point) > 10:
            return None
        side = None
        if any(token in text for token in ("home", "team 1", "команда 1", "фора 1", "ф1")):
            side = "home"
        elif any(token in text for token in ("away", "team 2", "команда 2", "фора 2", "ф2")):
            side = "away"
        elif canonicalize_team_name(match.home_team) in canonicalize_team_name(text):
            side = "home"
        elif canonicalize_team_name(match.away_team) in canonicalize_team_name(text):
            side = "away"
        if side is None:
            return None
        selection = match.home_team if side == "home" else match.away_team
        return "spreads", selection, point, side, f"spreads:{side}:{point:g}"
    if text in {"п1", "1", "home", "home win", "победа 1"} or "победа хозя" in text:
        return "h2h", match.home_team, None, "home", "h2h:home"
    if text in {"п2", "2", "away", "away win", "победа 2"} or "победа гост" in text:
        return "h2h", match.away_team, None, "away", "h2h:away"
    if text in {"x", "draw", "ничья"}:
        return "h2h", "Draw", None, None, "h2h:draw"
    if "обе забьют" in text or "both teams to score" in text or "btts" in text:
        if any(token in text for token in ("yes", "да")):
            return "btts", "Yes", None, None, "btts:yes"
        if any(token in text for token in ("no", "нет")):
            return "btts", "No", None, None, "btts:no"
    return None


def parse_offers(rows: list[dict[str, Any]], match: Match, source_event_id: str) -> list[Offer]:
    offers: list[Offer] = []
    seen: set[tuple[Any, ...]] = set()
    for row in rows:
        name = str(row.get("name") or row.get("label") or "").strip()
        price = as_price(row.get("value") or row.get("odds") or row.get("price"))
        if not name or price is None or not 1.0 < price <= 100.0:
            continue
        parsed = parse_market_name(name, match)
        if parsed is None:
            continue
        family, selection, point, side, market_key = parsed
        identity = (family, selection, point, side, round(price, 4))
        if identity in seen:
            continue
        seen.add(identity)
        offers.append(
            Offer(
                source="sstats_pari",
                bookmaker="Pari",
                family=family,
                selection=selection,
                price=price,
                point=point,
                team_side=side,
                market_name=name,
                market_key=market_key,
                source_event_id=source_event_id,
                metadata={
                    "provider": "sstats_pari",
                    "independent_odds_source": True,
                    "raw_outcome_id": row.get("id") or row.get("outcomeId"),
                    "raw_name": name,
                    "fetched_at_utc": datetime.now(UTC).isoformat(),
                    "market_scope": "full_time",
                },
            )
        )
    return offers
