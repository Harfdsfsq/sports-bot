from __future__ import annotations

"""Parse Bzzoiro v2 bookmaker comparison odds into HARIZON provider hints.

Before this patch the bridge mostly understood /events/{id}/odds/ consensus
fields.  Bzzoiro v2 also exposes /events/{id}/odds/comparison/ and paginated
odds rows, which are better second-source line evidence.  The exact offer bridge
already turns provider_odds_hints into real Offer rows; this patch feeds it a
larger and normalized hint set.
"""

import json
import os
import re
from pathlib import Path
from typing import Any

_INSTALLED = False
_ORIGINAL_HINTS = None
_ORIGINAL_FETCH_V2 = None


def _truthy(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on", "y"}


def _to_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        text = str(value).strip().replace(",", ".")
        return float(text)
    except Exception:
        return None


def _norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9_]+", "_", str(value or "").strip().lower()).strip("_")


def _point_from_text(*values: Any) -> float | None:
    for value in values:
        if value in (None, ""):
            continue
        numeric = _to_float(value)
        if numeric is not None and 0.25 <= numeric <= 12.0:
            return round(float(numeric), 3)
        text = str(value).lower().replace(",", ".")
        match = re.search(r"(?:over_under_|over|under|ou|total|goals?|_)(\d+(?:\.\d+)?)", text)
        if match:
            number = float(match.group(1))
            if number in {15.0, 25.0, 35.0, 45.0, 55.0}:
                number = number / 10.0
            if 0.25 <= number <= 12.0:
                return round(number, 3)
        match = re.search(r"(\d)5(?:_goals)?", text)
        if match:
            return float(f"{match.group(1)}.5")
    return None


def _family_selection_point(market: Any, outcome: Any, row: dict[str, Any]) -> tuple[str | None, str | None, float | None]:
    market_norm = _norm(market or row.get("market_name") or row.get("market_key") or row.get("bet") or row.get("type"))
    outcome_norm = _norm(outcome or row.get("selection") or row.get("outcome") or row.get("name") or row.get("option_name"))
    option_value = row.get("option_value") or row.get("line") or row.get("point") or row.get("handicap") or row.get("total")

    if market_norm in {"home_win", "draw", "away_win"}:
        return "h2h", {"home_win": "home", "draw": "draw", "away_win": "away"}[market_norm], None
    if market_norm in {"1x2", "match_result", "moneyline", "winner"}:
        mapping = {
            "home": "home", "h": "home", "home_win": "home", "1": "home",
            "draw": "draw", "d": "draw", "x": "draw",
            "away": "away", "a": "away", "away_win": "away", "2": "away",
        }
        return "h2h", mapping.get(outcome_norm), None

    if market_norm in {"btts", "both_teams_to_score"} or market_norm.startswith("btts_"):
        mapping = {"yes": "Yes", "y": "Yes", "true": "Yes", "no": "No", "n": "No", "false": "No"}
        if market_norm.endswith("yes"):
            return "btts", "Yes", None
        if market_norm.endswith("no"):
            return "btts", "No", None
        return "btts", mapping.get(outcome_norm), None

    if "over_under" in market_norm or "overunder" in market_norm or "goals" in market_norm or "total" in market_norm:
        selection = None
        if outcome_norm in {"under", "u", "below"}:
            selection = "Under"
        elif outcome_norm in {"over", "o", "above"}:
            selection = "Over"
        elif "under" in market_norm and "over" not in market_norm.replace("over_under", ""):
            selection = "Under"
        elif "over" in market_norm and "under" not in market_norm.replace("over_under", ""):
            selection = "Over"
        point = _point_from_text(option_value, market_norm, row.get("market_name"), row.get("label"))
        if selection and point is not None:
            return "totals", selection, point

    if "spread" in market_norm or "handicap" in market_norm or "asian" in market_norm:
        selection = None
        if outcome_norm in {"home", "h", "1"}:
            selection = "home"
        elif outcome_norm in {"away", "a", "2"}:
            selection = "away"
        point = _point_from_text(option_value, row.get("handicap"), row.get("point"))
        if selection and point is not None:
            return "spreads", selection, point
    return None, None, None


def _price_from_row(row: dict[str, Any]) -> float | None:
    for key in (
        "decimal_odds", "price", "odds", "value", "best_price", "max_decimal_odds",
        "odd", "current", "quote", "decimal", "coefficient",
    ):
        price = _to_float(row.get(key))
        if price is not None and price > 1.0:
            return round(price, 4)
    implied = _to_float(row.get("implied_probability") or row.get("probability"))
    if implied and 0.01 < implied < 1.0:
        return round(1.0 / implied, 4)
    return None


def _bookmaker_from_row(row: dict[str, Any], fallback: str = "Bzzoiro") -> str:
    for key in ("bookmaker_name", "bookmaker", "bookmaker_slug", "provider", "source_name", "site"):
        value = row.get(key)
        if value:
            return str(value).strip()
    return fallback


def _add_hint(out: list[dict[str, Any]], *, family: str | None, selection: str | None, point: float | None, price: float | None, bookmaker: str, market_name: str, row: dict[str, Any]) -> None:
    if not family or not selection or price is None or price <= 1.0:
        return
    if family not in {"h2h", "totals", "spreads", "btts"}:
        return
    out.append({
        "source": "bzzoiro",
        "bookmaker": bookmaker or "Bzzoiro",
        "family": family,
        "selection": selection,
        "price": round(float(price), 4),
        "point": point,
        "market_name": market_name or str(row.get("market") or row.get("market_name") or ""),
        "market_key": family,
        "metadata": {
            "bzzoiro_odds_comparison_bridge": True,
            "bookmaker_slug": row.get("bookmaker_slug"),
            "updated_at": row.get("updated_at") or row.get("observed_at") or row.get("last_updated"),
            "is_max_quote": row.get("is_max_quote"),
        },
    })


def _scan_row(row: dict[str, Any], out: list[dict[str, Any]], *, market_hint: Any = None, outcome_hint: Any = None, bookmaker_hint: str | None = None) -> None:
    price = _price_from_row(row)
    if price is None:
        return
    market = market_hint or row.get("market") or row.get("market_name") or row.get("market_key") or row.get("bet") or row.get("type")
    outcome = outcome_hint or row.get("outcome") or row.get("selection") or row.get("option_name") or row.get("name")
    family, selection, point = _family_selection_point(market, outcome, row)
    _add_hint(
        out,
        family=family,
        selection=selection,
        point=point,
        price=price,
        bookmaker=bookmaker_hint or _bookmaker_from_row(row),
        market_name=str(market or ""),
        row=row,
    )


def _scan_comparison(
    payload: Any,
    out: list[dict[str, Any]],
    *,
    market_hint: Any = None,
    outcome_hint: Any = None,
    bookmaker_hint: str | None = None,
    depth: int = 0,
) -> None:
    if depth > 8:
        return
    if isinstance(payload, list):
        for item in payload:
            _scan_comparison(item, out, market_hint=market_hint, outcome_hint=outcome_hint, bookmaker_hint=bookmaker_hint, depth=depth + 1)
        return
    if not isinstance(payload, dict):
        return

    if _price_from_row(payload) is not None:
        _scan_row(payload, out, market_hint=market_hint, outcome_hint=outcome_hint, bookmaker_hint=bookmaker_hint)

    # Best-odds shape: {market: 1x2, best_odds: [{outcome, decimal_odds, bookmaker_slug}]}
    if isinstance(payload.get("best_odds"), list):
        for item in payload.get("best_odds") or []:
            if isinstance(item, dict):
                _scan_row(item, out, market_hint=payload.get("market") or market_hint, outcome_hint=item.get("outcome"), bookmaker_hint=_bookmaker_from_row(item, bookmaker_hint or "Bzzoiro"))

    markets = payload.get("markets") if isinstance(payload.get("markets"), dict) else None
    if markets:
        for market_name, market_payload in markets.items():
            _scan_comparison(market_payload, out, market_hint=market_name, outcome_hint=outcome_hint, bookmaker_hint=bookmaker_hint, depth=depth + 1)

    # Generic nested dicts.  Keys in comparison payloads are often bookmaker slugs
    # first, then outcomes, or market names first, then bookmaker slugs.
    for key, value in payload.items():
        if key in {"markets", "best_odds"}:
            continue
        if not isinstance(value, (dict, list)):
            continue
        key_norm = _norm(key)
        next_market = market_hint
        next_bookmaker = bookmaker_hint
        next_outcome = outcome_hint
        # Common market keys should become market hints; other string keys in a
        # market dict are safer as bookmaker hints.
        if key_norm in {"home", "away", "draw", "home_win", "away_win", "over", "under", "yes", "no", "h", "a", "d", "x", "1", "2", "home_team", "away_team", "homewin", "awaywin"}:
            next_outcome = key
        elif any(token in key_norm for token in ("over_under", "overunder", "btts", "1x2", "match_result", "spread", "handicap", "total", "goals")):
            next_market = key
        elif bookmaker_hint is None:
            next_bookmaker = key
        _scan_comparison(value, out, market_hint=next_market, outcome_hint=next_outcome, bookmaker_hint=next_bookmaker, depth=depth + 1)


def _consensus_hints(resources: dict[str, Any]) -> list[dict[str, Any]]:
    odds_payload = resources.get("odds") if isinstance(resources, dict) else None
    odds = odds_payload.get("odds") if isinstance(odds_payload, dict) and isinstance(odds_payload.get("odds"), dict) else odds_payload
    if not isinstance(odds, dict):
        return []
    mapping = {
        "home_win": ("h2h", "home", None),
        "draw": ("h2h", "draw", None),
        "away_win": ("h2h", "away", None),
        "over_15_goals": ("totals", "Over", 1.5),
        "under_15_goals": ("totals", "Under", 1.5),
        "over_25_goals": ("totals", "Over", 2.5),
        "under_25_goals": ("totals", "Under", 2.5),
        "over_35_goals": ("totals", "Over", 3.5),
        "under_35_goals": ("totals", "Under", 3.5),
        "btts_yes": ("btts", "Yes", None),
        "btts_no": ("btts", "No", None),
    }
    hints: list[dict[str, Any]] = []
    for key, (family, selection, point) in mapping.items():
        price = _to_float(odds.get(key))
        _add_hint(hints, family=family, selection=selection, point=point, price=price, bookmaker="BzzoiroConsensus", market_name=key, row={"market": key})
    return hints


def _enhanced_bzzoiro_odds_hints(resources: dict[str, Any]) -> list[dict[str, Any]]:
    hints: list[dict[str, Any]] = []
    try:
        if callable(_ORIGINAL_HINTS):
            hints.extend(_ORIGINAL_HINTS(resources) or [])
    except Exception:
        pass
    if isinstance(resources, dict):
        hints.extend(_consensus_hints(resources))
        for key in ("odds_comparison", "comparison", "odds_rows", "best_odds", "odds_best"):
            payload = resources.get(key)
            if payload is not None:
                _scan_comparison(payload, hints)
        # Some providers put comparison rows inside resources["odds"].
        odds_payload = resources.get("odds")
        if isinstance(odds_payload, dict) and ("markets" in odds_payload or "results" in odds_payload or "data" in odds_payload):
            _scan_comparison(odds_payload, hints)

    deduped: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for hint in hints:
        if not isinstance(hint, dict):
            continue
        family = str(hint.get("family") or "").strip()
        selection = str(hint.get("selection") or "").strip()
        price = _to_float(hint.get("price"))
        if family not in {"h2h", "totals", "spreads", "btts"} or not selection or price is None or price <= 1.0:
            continue
        point = _to_float(hint.get("point"))
        key = (
            family,
            selection.lower(),
            None if point is None else round(point, 3),
            str(hint.get("bookmaker") or "").strip().lower(),
            round(price, 4),
        )
        if key in seen:
            continue
        seen.add(key)
        normalized = dict(hint)
        normalized["source"] = "bzzoiro"
        normalized["price"] = round(price, 4)
        normalized["point"] = None if point is None else round(point, 3)
        normalized.setdefault("bookmaker", "Bzzoiro")
        normalized.setdefault("market_key", family)
        deduped.append(normalized)
    return deduped


async def _patched_fetch_v2_resources(client: Any, headers: dict[str, str], event_id: Any, stats: dict[str, Any], max_requests: int) -> dict[str, Any]:
    resources = {}
    if callable(_ORIGINAL_FETCH_V2):
        resources = await _ORIGINAL_FETCH_V2(client, headers, event_id, stats, max_requests)
    if not isinstance(resources, dict):
        resources = {}
    if not _truthy(os.getenv("BZZOIRO_ODDS_COMPARISON_ENABLED"), True):
        return resources
    if event_id in (None, ""):
        return resources
    try:
        used = int(float(stats.get("requests") or 0))
    except Exception:
        used = 0
    if used >= max_requests:
        return resources
    try:
        from app.services import bzzoiro_context_gap_finalizer as gap

        url = f"https://sports.bzzoiro.com/api/v2/events/{event_id}/odds/comparison/"
        payload = await gap._fetch_json(client, url, headers, stats)
        if isinstance(payload, dict):
            resources["odds_comparison"] = payload
            stats["odds_comparison_resources"] = int(float(stats.get("odds_comparison_resources") or 0)) + 1
    except Exception as exc:
        try:
            stats["errors"] = int(float(stats.get("errors") or 0)) + 1
            stats["last_error"] = f"odds_comparison:{type(exc).__name__}: {exc}"
        except Exception:
            pass
    return resources


def _write_report(payload: dict[str, Any]) -> None:
    try:
        path = Path(".data/exports/latest-bzzoiro-odds-comparison-bridge-install.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def install() -> dict[str, Any]:
    global _INSTALLED, _ORIGINAL_HINTS, _ORIGINAL_FETCH_V2
    if _INSTALLED:
        return {"installed": True, "already_installed": True}
    try:
        from app.services import windowed_core_coverage_runtime_patch as wc
        from app.services import bzzoiro_context_gap_finalizer as gap
    except Exception as exc:
        return {"installed": False, "error": f"import:{type(exc).__name__}: {exc}"}

    _ORIGINAL_HINTS = getattr(wc, "_bzzoiro_odds_hints", None)
    _ORIGINAL_FETCH_V2 = getattr(gap, "_fetch_v2_resources", None)
    wc._bzzoiro_odds_hints = _enhanced_bzzoiro_odds_hints  # type: ignore[attr-defined]
    if callable(_ORIGINAL_FETCH_V2):
        gap._fetch_v2_resources = _patched_fetch_v2_resources  # type: ignore[attr-defined]
    os.environ.setdefault("BZZOIRO_ODDS_COMPARISON_ENABLED", "true")
    _INSTALLED = True
    report = {
        "installed": True,
        "patched_windowed_core_bzzoiro_odds_hints": callable(_ORIGINAL_HINTS),
        "patched_gap_fetch_v2_resources": callable(_ORIGINAL_FETCH_V2),
        "enabled": _truthy(os.getenv("BZZOIRO_ODDS_COMPARISON_ENABLED"), True),
    }
    _write_report(report)
    return report
