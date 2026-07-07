from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

REPORT = Path(".data/exports/latest-same-match-total-conflict-guard.json")
ART = Path("artifacts/run-bot/latest-same-match-total-conflict-guard.json")


def _norm(value: Any) -> str:
    text = str(value or "").strip().lower().replace("ё", "е")
    text = re.sub(r"[^a-z0-9а-я]+", " ", text)
    return " ".join(text.split())


def _family(candidate: dict[str, Any]) -> str:
    text = _norm(candidate.get("family") or candidate.get("market_family") or candidate.get("market"))
    if "total" in text or "тотал" in text or "goals" in text:
        return "totals"
    return text


def _side(candidate: dict[str, Any]) -> str:
    text = _norm(candidate.get("selection_key") or candidate.get("selection") or candidate.get("outcome"))
    if "under" in text or "меньше" in text or "тм" in text:
        return "under"
    if "over" in text or "больше" in text or "тб" in text:
        return "over"
    return text


def _match_key(candidate: dict[str, Any]) -> str:
    raw = candidate.get("canonical_match_id") or candidate.get("match_key")
    if raw:
        return _norm(raw)
    return "|".join([
        _norm(candidate.get("home_team") or candidate.get("home")),
        _norm(candidate.get("away_team") or candidate.get("away")),
        str(candidate.get("commence_time") or candidate.get("kickoff") or "")[:10],
    ])


def _num(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(str(value).replace(",", "."))
    except Exception:
        return default


def _write(payload: dict[str, Any]) -> None:
    for path in (REPORT, ART):
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        except Exception:
            pass


def install(base: Any) -> None:
    original = getattr(base, "select_top_picks", None)
    if not callable(original) or getattr(base, "_same_match_total_conflict_guard_installed", False):
        return

    def wrapped(viable: list[tuple], bankroll: dict[str, Any]) -> list:
        groups: dict[str, list[tuple]] = {}
        for item in viable:
            try:
                _, candidate, metrics, _tier = item
            except Exception:
                continue
            if not isinstance(candidate, dict) or not isinstance(metrics, dict):
                continue
            if _family(candidate) != "totals":
                continue
            side = _side(candidate)
            if side not in {"over", "under"}:
                continue
            if _num(metrics.get("canonical_ev_pct"), 0.0) <= 0 or _num(metrics.get("canonical_edge_pp"), 0.0) <= 0:
                continue
            groups.setdefault(_match_key(candidate), []).append(item)

        blocked: set[int] = set()
        rows: list[dict[str, Any]] = []
        for key, items in groups.items():
            sides = {_side(item[1]) for item in items if len(item) >= 3}
            if not {"over", "under"}.issubset(sides):
                continue
            for item in items:
                blocked.add(id(item))
            rows.append({
                "match_key": key,
                "blocked_count": len(items),
                "candidates": [
                    {
                        "home_team": item[1].get("home_team"),
                        "away_team": item[1].get("away_team"),
                        "selection": item[1].get("selection"),
                        "point": item[1].get("point"),
                        "odds": item[2].get("odds"),
                        "ev_pct": item[2].get("canonical_ev_pct"),
                        "edge_pp": item[2].get("canonical_edge_pp"),
                        "xg": item[2].get("xg_sanity"),
                    }
                    for item in items
                ],
            })

        if rows:
            filtered = [item for item in viable if id(item) not in blocked]
            _write({"status": "blocked_conflicts", "blocked_matches": rows, "input_viable": len(viable), "output_viable": len(filtered)})
            return original(filtered, bankroll)
        _write({"status": "ok_no_conflict", "input_viable": len(viable)})
        return original(viable, bankroll)

    base.select_top_picks = wrapped
    base._same_match_total_conflict_guard_installed = True
