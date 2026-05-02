from __future__ import annotations

import re
from typing import Any

try:
    from app.services.telegram_i18n import translate_selection_text, translate_team_name
except Exception:  # pragma: no cover
    def translate_selection_text(selection: Any, home_team: Any = "", away_team: Any = "") -> str:
        return str(selection or "")

    def translate_team_name(name: Any) -> str:
        return str(name or "")


def _num(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(str(value).replace(",", "."))
    except Exception:
        return None


def format_point(value: Any, *, signed: bool = False) -> str:
    number = _num(value)
    if number is None:
        return ""
    if abs(number) < 1e-9:
        return "0"
    text = f"{number:g}"
    if signed and number > 0:
        return f"+{text}"
    return text


def is_quarter_total_point(value: Any) -> bool:
    number = _num(value)
    if number is None:
        return False
    frac = abs(number) % 1.0
    return abs(frac - 0.25) <= 1e-6 or abs(frac - 0.75) <= 1e-6


def _selection_side(selection: str, home_team: Any = "", away_team: Any = "", team_side: Any = "") -> str:
    side = str(team_side or "").strip().lower()
    if side in {"home", "1", "team1"}:
        return "1"
    if side in {"away", "2", "team2"}:
        return "2"
    low = str(selection or "").lower()
    if any(token in low for token in ("ф1", "п1", "home", "хозя", " 1", "1 ")):
        return "1"
    if any(token in low for token in ("ф2", "п2", "away", "гост", " 2", "2 ")):
        return "2"
    home_candidates = {str(home_team or ""), translate_team_name(home_team)}
    away_candidates = {str(away_team or ""), translate_team_name(away_team)}
    for value in home_candidates:
        if value and value.lower() in low:
            return "1"
    for value in away_candidates:
        if value and value.lower() in low:
            return "2"
    return ""


def _kind_from_selection(selection: str, selection_key: Any = "") -> str:
    key = str(selection_key or "").strip().lower()
    if key in {"over", "under", "yes", "no", "home", "away", "draw"}:
        return key
    low = str(selection or "").strip().lower()
    if any(token in low for token in ("over", "больше", "тб")):
        return "over"
    if any(token in low for token in ("under", "меньше", "тм")):
        return "under"
    if any(token in low for token in ("yes", "да")):
        return "yes"
    if any(token in low for token in ("no", "нет")):
        return "no"
    if low in {"draw", "x", "ничья"} or "нич" in low:
        return "draw"
    return ""


def market_display(
    selection: Any,
    *,
    family: Any = "",
    point: Any = None,
    team_side: Any = "",
    home_team: Any = "",
    away_team: Any = "",
    selection_key: Any = "",
    include_family: bool = True,
) -> str:
    """Return a Russian publication-safe market string.

    Examples:
    - totals + Over + 2.5 -> "Тотал — Больше 2.5"
    - spreads + home + 0 -> "Фора — Ф1(0)"
    - h2h + away -> "Исход — П2"
    """
    raw_selection = str(selection or "").strip()
    text = translate_selection_text(raw_selection, home_team, away_team).strip()
    family_key = str(family or "").strip().lower().replace("_", "")
    kind = _kind_from_selection(text or raw_selection, selection_key)
    point_text = format_point(point)

    # If point is missing but embedded in the selection, extract it.
    if not point_text:
        match = re.search(r"\(([+\-]?\d+(?:[.,]\d+)?)\)", text) or re.search(r"\b([+\-]?\d+(?:[.,]\d+)?)\b", text)
        if match:
            point_text = format_point(match.group(1))

    is_total = family_key in {"totals", "total", "matchtotal"} or kind in {"over", "under"}
    is_team_total = family_key in {"teamtotals", "teamtotal", "individualtotals"}
    is_spread = family_key in {"spreads", "spread", "handicap", "dnb"}
    is_h2h = family_key in {"h2h", "moneyline", "winner", "1x2"}
    is_btts = family_key in {"btts", "bothteamstoscore"}

    if is_total:
        direction = "Больше" if kind == "over" else "Меньше" if kind == "under" else text
        result = f"{direction} {point_text}".strip()
        return f"Тотал — {result}" if include_family else result

    if is_team_total:
        side = _selection_side(text, home_team, away_team, team_side)
        direction = "Больше" if kind == "over" else "Меньше" if kind == "under" else text
        prefix = f"ИТ{side}" if side else "Инд. тотал"
        result = f"{prefix} — {direction} {point_text}".strip()
        return result if include_family else f"{direction} {point_text}".strip()

    if is_spread or (point_text and _selection_side(text, home_team, away_team, team_side)):
        side = _selection_side(text, home_team, away_team, team_side)
        signed = format_point(point if point not in (None, "") else point_text, signed=True) or "0"
        if side:
            result = f"Ф{side}({signed})"
            return f"Фора — {result}" if include_family else result
        return f"Фора — {text}" if include_family else text

    if is_h2h:
        side = _selection_side(text, home_team, away_team, team_side)
        if side == "1" or kind == "home":
            result = "П1"
        elif side == "2" or kind == "away":
            result = "П2"
        elif kind == "draw":
            result = "Ничья"
        else:
            result = text
        return f"Исход — {result}" if include_family else result

    if is_btts:
        result = "Да" if kind == "yes" else "Нет" if kind == "no" else text
        return f"Обе забьют — {result}" if include_family else result

    return text


def market_display_from_mapping(row: dict[str, Any], *, include_family: bool = True) -> str:
    return market_display(
        row.get("selection") or row.get("market") or "",
        family=row.get("family") or row.get("market_family") or row.get("market_key") or "",
        point=row.get("point") or row.get("line") or row.get("total") or row.get("handicap"),
        team_side=row.get("team_side") or row.get("side") or "",
        home_team=row.get("home_team") or row.get("home") or "",
        away_team=row.get("away_team") or row.get("away") or "",
        selection_key=row.get("selection_key") or "",
        include_family=include_family,
    )
