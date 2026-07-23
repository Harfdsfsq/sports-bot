"""Persistent decision/price ledger for Focused Alpha calibration and CLV.

Every run records compact pre-match observations. Repeated snapshots update a
candidate closing price, while canonical publication history supplies settlement for
matching public decisions. The ledger does not change publication behaviour.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
EXPORT = ROOT / ".data" / "exports"
LEDGER_PATH = EXPORT / "latest-focused-alpha-learning-ledger.json"
BOARD_PATH = EXPORT / "latest-focused-alpha-decisions.json"
HISTORY_PATH = EXPORT / "latest-focused-alpha-canonical-history.json"


def _load(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return default


def _write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _norm(value: Any) -> str:
    value = _text(value).lower().replace("ё", "е")
    value = re.sub(r"[^a-z0-9а-я]+", " ", value)
    return " ".join(value.split())


def _float(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        parsed = float(str(value).replace(",", "."))
        return parsed if math.isfinite(parsed) else None
    except Exception:
        return None


def _dt(value: Any) -> datetime | None:
    try:
        text = _text(value)
        if not text:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)
    except Exception:
        return None


def _point(value: Any) -> str:
    parsed = _float(value)
    return f"{parsed:g}" if parsed is not None else _norm(value)


def _selection(value: Any) -> str:
    text = _norm(value)
    if text in {"over", "under", "home", "away", "draw"}:
        return text
    if "меньше" in text or "under" in text:
        return "under"
    if "больше" in text or "over" in text:
        return "over"
    return text


def _semantic_tuple(row: dict[str, Any]) -> tuple[str, str, str, str, str]:
    match = _norm(row.get("match_key") or row.get("canonical_match_id"))
    home = _norm(row.get("home_team") or row.get("home"))
    away = _norm(row.get("away_team") or row.get("away"))
    if not match:
        kickoff = _text(row.get("commence_time") or row.get("commence_time_utc") or row.get("kickoff_utc"))[:10]
        match = f"{kickoff}|{home}|{away}"
    family = _norm(row.get("family") or row.get("market_family"))
    selection = _selection(row.get("selection_key") or row.get("selection"))
    point = _point(row.get("point") or row.get("line") or row.get("handicap"))
    return match, family, selection, point, f"{home}|{away}"


def _history_match(decision: dict[str, Any], history: list[dict[str, Any]]) -> dict[str, Any] | None:
    target = _semantic_tuple(decision)
    for row in history:
        if not isinstance(row, dict):
            continue
        current = _semantic_tuple(row)
        same_match = current[0] == target[0] or (current[4] and current[4] == target[4])
        if same_match and current[1:4] == target[1:4]:
            return row
    return None


def _compact(row: dict[str, Any], selected_keys: set[str], snapshot_at: str, run_id: str) -> dict[str, Any]:
    decision_key = _text(row.get("decision_key"))
    observation_id = hashlib.sha1(f"{run_id}|{decision_key}".encode()).hexdigest()
    return {
        "observation_id": observation_id,
        "run_id": run_id,
        "snapshot_at_utc": snapshot_at,
        "decision_key": decision_key,
        "match_key": row.get("match_key"),
        "home_team": row.get("home_team"),
        "away_team": row.get("away_team"),
        "league_name": row.get("league_name"),
        "kickoff_utc": row.get("commence_time"),
        "family": row.get("family"),
        "selection": row.get("selection"),
        "selection_key": row.get("selection_key"),
        "point": row.get("point"),
        "odds": row.get("odds"),
        "model_probability": row.get("model_probability"),
        "market_probability": row.get("market_probability"),
        "conservative_probability": row.get("conservative_probability"),
        "edge_pp": row.get("edge_pp"),
        "ev_pct": row.get("ev_pct"),
        "conservative_ev_pct": row.get("conservative_ev_pct"),
        "risk_adjusted_utility": row.get("risk_adjusted_utility"),
        "confidence": row.get("confidence"),
        "quality": row.get("quality"),
        "quality_source": row.get("quality_source"),
        "odds_sources_count": row.get("odds_sources_count"),
        "context_sources_count": row.get("context_sources_count"),
        "books_count": row.get("books_count"),
        "hard_xg": bool(row.get("hard_xg")),
        "movement_ok": bool(row.get("movement_ok")),
        "movement_status": row.get("movement_status"),
        "blockers": list(row.get("blockers") or []),
        "passes_shadow_contract": bool(row.get("passes_shadow_contract")),
        "selected_shadow": decision_key in selected_keys,
    }


def _decision_summary(observations: list[dict[str, Any]], history: list[dict[str, Any]]) -> dict[str, Any]:
    ordered = sorted(observations, key=lambda row: _text(row.get("snapshot_at_utc")))
    first = ordered[0]
    latest = ordered[-1]
    kickoff = _dt(latest.get("kickoff_utc"))
    pre_kickoff = [
        row
        for row in ordered
        if _dt(row.get("snapshot_at_utc")) is not None
        and (kickoff is None or _dt(row.get("snapshot_at_utc")) <= kickoff)
        and (_float(row.get("odds")) or 0.0) > 1.0
    ]
    closing = pre_kickoff[-1] if pre_kickoff else None
    selected = [row for row in ordered if row.get("selected_shadow")]
    taken = selected[0] if selected else None
    settled = _history_match(latest, history)
    published = bool(settled and (settled.get("telegram_sent") or settled.get("published_at") or settled.get("sent_at")))
    taken_odds = _float(settled.get("odds")) if published and settled else _float(taken.get("odds")) if taken else None
    closing_odds = _float(closing.get("odds")) if closing else None
    clv_pct = None
    if taken_odds and closing_odds and closing_odds > 1.0:
        clv_pct = round((taken_odds / closing_odds - 1.0) * 100.0, 4)
    result = _text(settled.get("result") or settled.get("status")) if settled else "pending"
    return {
        "decision_key": latest.get("decision_key"),
        "match_key": latest.get("match_key"),
        "home_team": latest.get("home_team"),
        "away_team": latest.get("away_team"),
        "league_name": latest.get("league_name"),
        "kickoff_utc": latest.get("kickoff_utc"),
        "family": latest.get("family"),
        "selection": latest.get("selection"),
        "selection_key": latest.get("selection_key"),
        "point": latest.get("point"),
        "snapshots": len(ordered),
        "first_snapshot_at_utc": first.get("snapshot_at_utc"),
        "latest_snapshot_at_utc": latest.get("snapshot_at_utc"),
        "selected_shadow_ever": bool(selected),
        "first_selected_shadow_at_utc": selected[0].get("snapshot_at_utc") if selected else None,
        "taken_or_shadow_odds": taken_odds,
        "closing_odds_candidate": closing_odds,
        "closing_snapshot_at_utc": closing.get("snapshot_at_utc") if closing else None,
        "clv_pct": clv_pct,
        "published": published,
        "result": result or "pending",
        "settled": result in {"won", "lost", "push", "void", "cancelled", "refunded"},
        "latest_risk_adjusted_utility": latest.get("risk_adjusted_utility"),
        "latest_conservative_ev_pct": latest.get("conservative_ev_pct"),
        "latest_blockers": latest.get("blockers") or [],
    }


def update_learning_ledger(board: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = board if isinstance(board, dict) else _load(BOARD_PATH, {})
    ranked = payload.get("ranked") if isinstance(payload.get("ranked"), list) else []
    selected = payload.get("selected_shadow") if isinstance(payload.get("selected_shadow"), list) else []
    selected_keys = {_text(row.get("decision_key")) for row in selected if isinstance(row, dict)}
    snapshot_at = _text(payload.get("created_at_utc")) or datetime.now(UTC).isoformat()
    run_id = _text(os.getenv("GITHUB_RUN_ID") or os.getenv("HARIZON_RUN_ID") or f"local:{snapshot_at}")

    existing = _load(LEDGER_PATH, {})
    observations = existing.get("observations") if isinstance(existing.get("observations"), list) else []
    by_id = {
        _text(row.get("observation_id")): dict(row)
        for row in observations
        if isinstance(row, dict) and _text(row.get("observation_id"))
    }
    for row in ranked[:100]:
        if not isinstance(row, dict) or not _text(row.get("decision_key")):
            continue
        compact = _compact(row, selected_keys, snapshot_at, run_id)
        by_id[compact["observation_id"]] = compact
    observations = sorted(by_id.values(), key=lambda row: _text(row.get("snapshot_at_utc")))[-10000:]

    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in observations:
        grouped.setdefault(_text(row.get("decision_key")), []).append(row)
    history = _load(HISTORY_PATH, [])
    history_rows = [row for row in history if isinstance(row, dict)] if isinstance(history, list) else []
    decisions = {
        key: _decision_summary(rows, history_rows)
        for key, rows in grouped.items()
        if key
    }
    settled = [row for row in decisions.values() if row.get("settled")]
    with_clv = [row for row in decisions.values() if row.get("clv_pct") is not None]
    result = {
        "status": "ok",
        "updated_at_utc": datetime.now(UTC).isoformat(),
        "policy": "append_compact_run_observations_and_latest_pre_kickoff_price",
        "observations": observations,
        "decisions": decisions,
        "summary": {
            "observations": len(observations),
            "unique_decisions": len(decisions),
            "selected_shadow_decisions": sum(bool(row.get("selected_shadow_ever")) for row in decisions.values()),
            "published_decisions": sum(bool(row.get("published")) for row in decisions.values()),
            "settled_decisions": len(settled),
            "decisions_with_clv": len(with_clv),
            "mean_clv_pct": round(sum(float(row["clv_pct"]) for row in with_clv) / len(with_clv), 4) if with_clv else None,
        },
        "publication_contract_relaxed": False,
    }
    _write(LEDGER_PATH, result)
    return result


__all__ = ["BOARD_PATH", "HISTORY_PATH", "LEDGER_PATH", "update_learning_ledger"]
