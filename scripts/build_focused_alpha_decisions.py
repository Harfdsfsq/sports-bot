"""Build the Focused Alpha shadow decision board.

This script never sends Telegram messages.  It records the complete candidate state
needed for calibration and chooses at most a small, diversified shadow portfolio.
Production publication remains behind the existing guards and an explicit future
FOCUSED_ALPHA_LIVE_ENABLED switch.
"""

from __future__ import annotations

import json
import math
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.services.focused_alpha_history import build_history_audit, league_prior

ROOT = Path(__file__).resolve().parents[1]
EXPORT = ROOT / ".data" / "exports"
OUT = EXPORT / "latest-focused-alpha-decisions.json"

CANDIDATE_PATHS = (
    EXPORT / "latest-rescue-candidates.json",
    EXPORT / "latest-controlled-rescue-candidates.json",
    EXPORT / "debug-candidates-before-quality.json",
    EXPORT / "debug-candidates-after-quality.json",
    EXPORT / "latest-picks.json",
    ROOT / "artifacts" / "run-bot" / "latest-rescue-candidates.json",
)


def _load(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return default


def _write(payload: Any) -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        result = float(str(value).replace(",", "."))
        return result if math.isfinite(result) else default
    except Exception:
        return default


def _probability(value: Any) -> float:
    result = _float(value, 0.0)
    if result > 1.0:
        result /= 100.0
    return max(0.0, min(1.0, result))


def _norm(value: Any) -> str:
    text = str(value or "").strip().lower().replace("ё", "е")
    text = re.sub(r"[^a-z0-9а-я]+", " ", text)
    return " ".join(text.split())


def _point(row: dict[str, Any]) -> str:
    value = row.get("point") or row.get("line") or row.get("handicap")
    try:
        return f"{float(str(value).replace(',', '.')):g}" if value not in (None, "") else ""
    except Exception:
        return _norm(value)


def _selection(row: dict[str, Any]) -> str:
    explicit = _norm(row.get("selection_key"))
    if explicit:
        return explicit
    text = _norm(row.get("selection"))
    if "меньше" in text or "under" in text:
        return "under"
    if "больше" in text or "over" in text:
        return "over"
    return text


def _key(row: dict[str, Any]) -> str:
    match = _norm(row.get("canonical_match_id") or row.get("match_key") or row.get("event_key"))
    if not match:
        match = "|".join(
            (
                _norm(row.get("home_team")),
                _norm(row.get("away_team")),
                str(row.get("commence_time") or row.get("kickoff_utc") or "")[:10],
            )
        )
    return "|".join((match, _norm(row.get("family") or row.get("market_family")), _selection(row), _point(row)))


def _rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [dict(row) for row in payload if isinstance(row, dict)]
    if not isinstance(payload, dict):
        return []
    result: list[dict[str, Any]] = []
    for key in (
        "rows",
        "items",
        "candidates",
        "bets",
        "selected_all",
        "published_candidates",
        "debug_candidates_before_quality",
        "debug_candidates_after_quality",
    ):
        value = payload.get(key)
        if isinstance(value, list):
            result.extend(dict(row) for row in value if isinstance(row, dict))
    for key in ("selected", "pick", "candidate"):
        value = payload.get(key)
        if isinstance(value, dict):
            result.append(dict(value))
    return result


def collect_candidates() -> list[dict[str, Any]]:
    best: dict[str, dict[str, Any]] = {}
    for path in CANDIDATE_PATHS:
        for row in _rows(_load(path, {})):
            row = dict(row)
            row["_focused_alpha_source_path"] = str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path)
            key = _key(row)
            if not key.strip("|"):
                continue
            current = best.get(key)
            score = sum(row.get(field) not in (None, "", [], {}) for field in (
                "odds",
                "adjusted_probability",
                "model_probability",
                "market_probability",
                "confidence",
                "quality_score",
                "ev_pct",
                "edge_pct",
                "confirmation_sources",
                "odds_sources_count",
                "commence_time",
            ))
            current_score = -1 if current is None else sum(current.get(field) not in (None, "", [], {}) for field in (
                "odds",
                "adjusted_probability",
                "model_probability",
                "market_probability",
                "confidence",
                "quality_score",
                "ev_pct",
                "edge_pct",
                "confirmation_sources",
                "odds_sources_count",
                "commence_time",
            ))
            if current is None or score >= current_score:
                best[key] = row
    return list(best.values())


def _source_count(row: dict[str, Any], role: str) -> int:
    keys = (
        ("odds_sources_count", "line_sources_count", "independent_odds_sources_count")
        if role == "odds"
        else ("confirmation_sources_count", "context_sources_count", "independent_context_sources_count")
    )
    numeric = max((_float(row.get(key), 0.0) for key in keys), default=0.0)
    list_keys = (
        ("odds_sources", "line_sources")
        if role == "odds"
        else ("confirmation_sources", "context_sources")
    )
    names: set[str] = set()
    for key in list_keys:
        value = row.get(key)
        if isinstance(value, list):
            names.update(_norm(item) for item in value if _norm(item))
        elif isinstance(value, str):
            names.update(_norm(item) for item in re.split(r"[,;+|/]", value) if _norm(item))
    return int(max(numeric, len(names)))


def _has_hard_xg(row: dict[str, Any]) -> bool:
    total = _float(row.get("total_xg"), -1.0)
    home = _float(row.get("expected_home"), -1.0)
    away = _float(row.get("expected_away"), -1.0)
    if total > 0 or (home >= 0 and away >= 0):
        source = _norm(row.get("xg_source") or row.get("context_source") or row.get("model_mode"))
        return not any(token in source for token in ("market implied", "proxy", "default"))
    sanity = row.get("xg_sanity") if isinstance(row.get("xg_sanity"), dict) else {}
    return bool(sanity.get("enabled")) and not bool(sanity.get("market_implied_only"))


def _movement_state(row: dict[str, Any]) -> tuple[bool, str]:
    for value in (
        row.get("line_movement_guard"),
        row.get("line_movement"),
        (row.get("source_summary") or {}).get("line_movement_guard") if isinstance(row.get("source_summary"), dict) else None,
    ):
        if not isinstance(value, dict):
            continue
        status = str(value.get("status") or value.get("line_movement_lifecycle_status") or "").strip()
        passed = bool(value.get("passed", status in {"movement_confirmed", "movement_ready", "publish_now_no_next_cron"}))
        return passed, status
    status = str(row.get("line_movement_lifecycle_status") or row.get("movement_status") or "").strip()
    return status in {"movement_confirmed", "movement_ready", "publish_now_no_next_cron"}, status


def _quality(row: dict[str, Any]) -> tuple[float, str]:
    payload = row.get("quality") if isinstance(row.get("quality"), dict) else {}
    score = max(
        _float(row.get("quality_score"), 0.0),
        _float(payload.get("quality_score"), 0.0),
        _float(row.get("publication_score"), 0.0),
    )
    source = _norm(row.get("quality_score_source") or payload.get("quality_score_source")) or "unknown"
    return score, source


def score_candidate(row: dict[str, Any], history: dict[str, Any]) -> dict[str, Any]:
    odds = _float(row.get("odds"), 0.0)
    adjusted = _probability(row.get("adjusted_probability") or row.get("adjusted_probability_pct"))
    model = _probability(row.get("model_probability") or row.get("model_probability_pct"))
    market = _probability(row.get("market_probability") or row.get("market_probability_pct"))
    probability = adjusted or model
    implied = 1.0 / odds if odds > 1.0 else 0.0
    canonical_edge = (probability - implied) * 100.0 if implied else -100.0
    canonical_ev = (probability * odds - 1.0) * 100.0 if odds > 1.0 else -100.0
    declared_edge = _float(row.get("canonical_edge_pp") or row.get("edge_pct") or row.get("edge_pp"), canonical_edge)
    declared_ev = _float(row.get("canonical_ev_pct") or row.get("ev_pct"), canonical_ev)
    edge = min(canonical_edge, declared_edge) if probability else declared_edge
    ev = min(canonical_ev, declared_ev) if probability else declared_ev
    confidence = _float(row.get("confidence"), 0.0)
    quality, quality_source = _quality(row)
    odds_sources = _source_count(row, "odds")
    context_sources = _source_count(row, "context")
    books = int(_float(row.get("books_count") or row.get("bookmaker_count"), 0.0))
    if books > 25:
        books = 0
    hard_xg = _has_hard_xg(row)
    movement_ok, movement_status = _movement_state(row)
    prior = league_prior(row.get("league_name"), history)

    # Lower confidence bound: the model probability is shrunk toward market price
    # according to missing evidence.  This is deliberately more conservative than
    # ranking by raw EV.
    uncertainty = 0.025
    uncertainty += 0.025 if odds_sources < 2 else 0.0
    uncertainty += 0.035 if context_sources < 2 else 0.0
    uncertainty += 0.025 if not hard_xg else 0.0
    uncertainty += 0.020 if quality_source in {"proxy", "unknown", "raw missing"} else 0.0
    uncertainty += 0.015 if books < 3 else 0.0
    conservative_probability = max(implied, probability - uncertainty) if probability else 0.0
    conservative_ev = (conservative_probability * odds - 1.0) * 100.0 if odds > 1.0 else -100.0

    utility = 0.0
    utility += conservative_ev * 1.8
    utility += max(-5.0, min(12.0, edge)) * 1.2
    utility += min(12.0, quality / 8.0)
    utility += min(10.0, confidence / 10.0)
    utility += min(8.0, odds_sources * 3.5)
    utility += min(10.0, context_sources * 4.0)
    utility += min(6.0, books * 1.5)
    utility += 7.0 if hard_xg else -8.0
    utility += 6.0 if movement_ok else -7.0
    utility += prior["reliability"] * 2.0 + prior["profit_signal"] * 3.0
    if odds < 1.70 or odds > 3.10:
        utility -= 15.0
    if market and probability and abs(probability - market) > 0.16:
        utility -= 10.0

    blockers: list[str] = []
    if odds <= 1.0:
        blockers.append("missing_or_invalid_odds")
    if probability <= 0.0:
        blockers.append("missing_model_probability")
    if conservative_ev < _float(os.getenv("FOCUSED_ALPHA_MIN_CONSERVATIVE_EV_PCT"), 2.0):
        blockers.append("conservative_ev_below_min")
    if edge < _float(os.getenv("FOCUSED_ALPHA_MIN_EDGE_PP"), 2.0):
        blockers.append("edge_below_min")
    if quality < _float(os.getenv("FOCUSED_ALPHA_MIN_QUALITY"), 68.0):
        blockers.append("quality_below_min")
    if confidence < _float(os.getenv("FOCUSED_ALPHA_MIN_CONFIDENCE"), 68.0):
        blockers.append("confidence_below_min")
    if odds_sources < 2:
        blockers.append("odds_sources_below_2")
    if context_sources < 2:
        blockers.append("context_sources_below_2")
    if books < 2:
        blockers.append("bookmaker_quorum_below_2")
    if not hard_xg:
        blockers.append("hard_xg_missing")
    if not movement_ok:
        blockers.append("movement_not_confirmed")

    return {
        "decision_key": _key(row),
        "match_key": row.get("match_key") or row.get("canonical_match_id"),
        "home_team": row.get("home_team"),
        "away_team": row.get("away_team"),
        "league_name": row.get("league_name"),
        "commence_time": row.get("commence_time") or row.get("kickoff_utc"),
        "family": row.get("family") or row.get("market_family"),
        "selection": row.get("selection"),
        "selection_key": _selection(row),
        "point": row.get("point") or row.get("line"),
        "odds": odds,
        "model_probability": round(probability, 6),
        "market_probability": round(market, 6),
        "conservative_probability": round(conservative_probability, 6),
        "edge_pp": round(edge, 3),
        "ev_pct": round(ev, 3),
        "conservative_ev_pct": round(conservative_ev, 3),
        "confidence": round(confidence, 3),
        "quality": round(quality, 3),
        "quality_source": quality_source,
        "odds_sources_count": odds_sources,
        "context_sources_count": context_sources,
        "books_count": books,
        "hard_xg": hard_xg,
        "movement_ok": movement_ok,
        "movement_status": movement_status,
        "uncertainty_margin_probability": round(uncertainty, 6),
        "risk_adjusted_utility": round(utility, 3),
        "history_prior": prior,
        "blockers": blockers,
        "passes_shadow_contract": not blockers,
        "source_path": row.get("_focused_alpha_source_path"),
        "raw_decision_snapshot": row,
    }


def build_decisions() -> dict[str, Any]:
    history = build_history_audit()
    scored = [score_candidate(row, history) for row in collect_candidates()]
    scored.sort(key=lambda row: (-_float(row.get("risk_adjusted_utility")), -_float(row.get("conservative_ev_pct")), str(row.get("decision_key"))))
    maximum = max(0, min(3, int(_float(os.getenv("FOCUSED_ALPHA_DAILY_MAX_DECISIONS"), 2.0))))
    selected: list[dict[str, Any]] = []
    matches: set[str] = set()
    leagues: set[str] = set()
    for row in scored:
        if not row.get("passes_shadow_contract"):
            continue
        match = _norm(row.get("match_key") or f"{row.get('home_team')}|{row.get('away_team')}")
        league = _norm(row.get("league_name"))
        if match in matches or (league and league in leagues):
            continue
        selected.append(row)
        matches.add(match)
        if league:
            leagues.add(league)
        if len(selected) >= maximum:
            break
    live_enabled = str(os.getenv("FOCUSED_ALPHA_LIVE_ENABLED") or "false").strip().lower() in {"1", "true", "yes", "on"}
    payload = {
        "status": "ok",
        "created_at_utc": datetime.now(UTC).isoformat(),
        "mode": "shadow" if not live_enabled else "live_eligible_but_existing_guards_still_required",
        "candidates_seen": len(scored),
        "passes_shadow_contract": sum(bool(row.get("passes_shadow_contract")) for row in scored),
        "selected_count": len(selected),
        "daily_max_decisions": maximum,
        "publication_minimum_count": 0,
        "no_bet_is_valid": True,
        "history_live_learning_ready": bool(history.get("live_learning_ready")),
        "thresholds_auto_tuned": False,
        "focused_alpha_live_enabled": live_enabled,
        "selected_shadow": selected,
        "ranked": scored[:100],
        "publication_contract_relaxed": False,
    }
    _write(payload)
    return payload


def main() -> int:
    payload = build_decisions()
    print(json.dumps({
        "status": payload.get("status"),
        "mode": payload.get("mode"),
        "candidates_seen": payload.get("candidates_seen"),
        "passes_shadow_contract": payload.get("passes_shadow_contract"),
        "selected_count": payload.get("selected_count"),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
