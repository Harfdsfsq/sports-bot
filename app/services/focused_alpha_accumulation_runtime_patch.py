"""Build a bounded, settlement-backed Focused Alpha accumulation portfolio.

This module never publishes a Telegram pick. It wraps the existing learning-ledger
update, selects at most a small number of economically relevant near-misses for
observation, stores them through the existing ``shadow_bets`` state path, and lets the
normal settlement service grade them on later runs. Strict live publication guards are
not changed.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
EXPORT = ROOT / ".data" / "exports"
ACCUMULATION_PATH = EXPORT / "latest-focused-alpha-accumulation.json"
_FINAL_RESULTS = {
    "won",
    "lost",
    "push",
    "void",
    "cancelled",
    "refunded",
    "half_won",
    "half_lost",
}
_INSTALLED = False


def _load(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return default


def _write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    tmp.replace(path)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _norm(value: Any) -> str:
    text = _text(value).lower().replace("ё", "е")
    text = re.sub(r"[^a-z0-9а-я]+", " ", text)
    return " ".join(text.split())


def _float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        parsed = float(str(value).replace(",", "."))
        return parsed if math.isfinite(parsed) else default
    except Exception:
        return default


def _int(value: Any, default: int = 0) -> int:
    try:
        return max(0, int(float(str(value))))
    except Exception:
        return default


def _dt(value: Any) -> datetime | None:
    raw = _text(value)
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _selection(value: Any) -> str:
    text = _norm(value)
    if "меньше" in text or "under" in text:
        return "under"
    if "больше" in text or "over" in text:
        return "over"
    return text


def _point(value: Any) -> str:
    parsed = _float(value, float("nan"))
    return f"{parsed:g}" if math.isfinite(parsed) else _norm(value)


def _names(value: Any) -> set[str]:
    if isinstance(value, (list, tuple, set)):
        raw = value
    elif isinstance(value, str):
        raw = re.split(r"[,;+|/]", value)
    else:
        raw = []
    return {_norm(item) for item in raw if _norm(item)}


def _dict(payload: Any, *keys: str) -> dict[str, Any]:
    current = payload
    for key in keys:
        if not isinstance(current, dict):
            return {}
        current = current.get(key)
    return current if isinstance(current, dict) else {}


def _evidence(row: dict[str, Any]) -> dict[str, list[str]]:
    raw = row.get("raw_decision_snapshot")
    raw = raw if isinstance(raw, dict) else {}
    truth = _dict(raw, "diagnostics", "focused_alpha_evidence_truth")
    summary = raw.get("source_summary") if isinstance(raw.get("source_summary"), dict) else {}
    coverage = summary.get("publish_coverage_contract")
    coverage = coverage if isinstance(coverage, dict) else {}
    odds: set[str] = set()
    contexts: set[str] = set()
    books: set[str] = set()
    xg: set[str] = set()
    for source in (row, raw, truth, summary, coverage):
        if not isinstance(source, dict):
            continue
        for key in ("odds_sources", "line_sources"):
            odds.update(_names(source.get(key)))
        for key in ("context_sources", "confirmation_sources"):
            contexts.update(_names(source.get(key)))
        for key in ("bookmakers", "books"):
            books.update(_names(source.get(key)))
        xg.update(_names(source.get("xg_sources")))
        bookmaker = _norm(source.get("bookmaker") or source.get("selected_bookmaker"))
        if bookmaker:
            books.add(bookmaker)
    return {
        "odds_sources": sorted(odds),
        "context_sources": sorted(contexts),
        "bookmakers": sorted(books),
        "xg_sources": sorted(xg),
    }


def _limits() -> dict[str, float | int]:
    return {
        "daily_max": max(0, min(5, _int(os.getenv("FOCUSED_ALPHA_ACCUMULATION_DAILY_MAX"), 2))),
        "min_odds": _float(os.getenv("FOCUSED_ALPHA_ACCUMULATION_MIN_ODDS"), 1.65),
        "max_odds": _float(os.getenv("FOCUSED_ALPHA_ACCUMULATION_MAX_ODDS"), 3.10),
        "min_edge_pp": _float(os.getenv("FOCUSED_ALPHA_ACCUMULATION_MIN_EDGE_PP"), 1.0),
        "min_ev_pct": _float(os.getenv("FOCUSED_ALPHA_ACCUMULATION_MIN_EV_PCT"), 0.0),
        "min_quality": _float(os.getenv("FOCUSED_ALPHA_ACCUMULATION_MIN_QUALITY"), 55.0),
        "min_confidence": _float(os.getenv("FOCUSED_ALPHA_ACCUMULATION_MIN_CONFIDENCE"), 60.0),
        "min_books": max(1, _int(os.getenv("FOCUSED_ALPHA_ACCUMULATION_MIN_BOOKS"), 2)),
    }


def _reject_reasons(row: dict[str, Any], limits: dict[str, float | int], now: datetime) -> list[str]:
    reasons: list[str] = []
    odds = _float(row.get("odds"))
    probability = _float(row.get("model_probability"))
    blockers = {_norm(item) for item in (row.get("blockers") or [])}
    kickoff = _dt(row.get("commence_time"))
    if not float(limits["min_odds"]) <= odds <= float(limits["max_odds"]):
        reasons.append("odds_outside_accumulation_window")
    if probability <= 0.0:
        reasons.append("missing_model_probability")
    if _float(row.get("edge_pp"), -100.0) < float(limits["min_edge_pp"]):
        reasons.append("edge_below_accumulation_min")
    if _float(row.get("ev_pct"), -100.0) < float(limits["min_ev_pct"]):
        reasons.append("ev_below_accumulation_min")
    if _float(row.get("quality")) < float(limits["min_quality"]):
        reasons.append("quality_below_accumulation_min")
    if _float(row.get("confidence")) < float(limits["min_confidence"]):
        reasons.append("confidence_below_accumulation_min")
    if _int(row.get("books_count")) < int(limits["min_books"]):
        reasons.append("bookmaker_quorum_below_accumulation_min")
    if _norm(row.get("family")) not in {"totals", "spreads"}:
        reasons.append("family_not_accumulation_enabled")
    if kickoff is None or kickoff <= now + timedelta(minutes=20):
        reasons.append("kickoff_not_actionable")
    if any("xg direction conflict" in value or "xg_direction_conflict" in value for value in blockers):
        reasons.append("xg_direction_conflict")
    if any("price outlier" in value or "price_outlier" in value for value in blockers):
        reasons.append("price_integrity_outlier")
    if any("same match total conflict" in value or "same_match_total_conflict" in value for value in blockers):
        reasons.append("same_match_total_conflict")
    return reasons


def _unit_pnl(result: str, odds: float) -> float | None:
    outcome = _text(result).lower()
    if outcome == "won" and odds > 1.0:
        return round(odds - 1.0, 6)
    if outcome == "half_won" and odds > 1.0:
        return round((odds - 1.0) / 2.0, 6)
    if outcome == "lost":
        return -1.0
    if outcome == "half_lost":
        return -0.5
    if outcome in {"push", "void", "cancelled", "refunded"}:
        return 0.0
    return None


def _observation(row: dict[str, Any], snapshot_at: str, run_id: str) -> dict[str, Any]:
    key = _text(row.get("decision_key"))
    evidence = _evidence(row)
    return {
        "observation_id": hashlib.sha1(f"{run_id}|{key}".encode()).hexdigest(),
        "run_id": run_id,
        "snapshot_at_utc": snapshot_at,
        "decision_key": key,
        "match_key": row.get("match_key"),
        "kickoff_utc": row.get("commence_time"),
        "odds": row.get("odds"),
        "model_probability": row.get("model_probability"),
        "market_probability": row.get("market_probability"),
        "conservative_probability": row.get("conservative_probability"),
        "edge_pp": row.get("edge_pp"),
        "ev_pct": row.get("ev_pct"),
        "conservative_ev_pct": row.get("conservative_ev_pct"),
        "risk_adjusted_utility": row.get("risk_adjusted_utility"),
        "quality": row.get("quality"),
        "confidence": row.get("confidence"),
        "books_count": row.get("books_count"),
        "odds_sources": evidence["odds_sources"],
        "context_sources": evidence["context_sources"],
        "bookmakers": evidence["bookmakers"],
        "xg_sources": evidence["xg_sources"],
        "hard_xg": bool(row.get("hard_xg")),
        "movement_ok": bool(row.get("movement_ok")),
        "blockers": list(row.get("blockers") or []),
    }


def _selection_record(row: dict[str, Any], snapshot_at: str) -> dict[str, Any]:
    observation = _observation(row, snapshot_at, "selection")
    observation.pop("observation_id", None)
    observation.pop("run_id", None)
    observation["selected_at_utc"] = snapshot_at
    observation["selection_mode"] = "accumulation"
    observation["home_team"] = row.get("home_team")
    observation["away_team"] = row.get("away_team")
    observation["league_name"] = row.get("league_name")
    observation["family"] = row.get("family")
    observation["selection"] = row.get("selection")
    observation["selection_key"] = row.get("selection_key")
    observation["point"] = row.get("point")
    observation["result"] = "pending"
    observation["settled"] = False
    observation["published"] = False
    observation["telegram_sent"] = False
    observation["publication_contract_relaxed"] = False
    return observation


def _choose(
    ranked: list[dict[str, Any]],
    selections: dict[str, dict[str, Any]],
    strict_keys: set[str],
    snapshot_at: str,
) -> tuple[list[dict[str, Any]], dict[str, int], dict[str, float | int]]:
    limits = _limits()
    day = snapshot_at[:10]
    selected_today = {
        key
        for key, row in selections.items()
        if _text(row.get("selected_at_utc"))[:10] == day
    }
    remaining = max(0, int(limits["daily_max"]) - len(selected_today | strict_keys))
    now = _dt(snapshot_at) or datetime.now(UTC)
    chosen: list[dict[str, Any]] = []
    matches: set[str] = set()
    leagues: set[str] = set()
    rejected: dict[str, int] = {}
    for row in ranked:
        if not isinstance(row, dict):
            continue
        key = _text(row.get("decision_key"))
        if not key or key in selections or key in strict_keys:
            continue
        reasons = _reject_reasons(row, limits, now)
        if reasons:
            for reason in reasons:
                rejected[reason] = rejected.get(reason, 0) + 1
            continue
        match = _norm(row.get("match_key") or f"{row.get('home_team')}|{row.get('away_team')}")
        league = _norm(row.get("league_name"))
        if match in matches or (league and league in leagues):
            rejected["diversification_duplicate"] = rejected.get("diversification_duplicate", 0) + 1
            continue
        chosen.append(row)
        matches.add(match)
        if league:
            leagues.add(league)
        if len(chosen) >= remaining:
            break
    return chosen, rejected, limits


def _candidate_bet(row: dict[str, Any]) -> Any | None:
    try:
        from app.schemas import CandidateBet
    except Exception:
        return None
    kickoff = _dt(row.get("commence_time"))
    odds = _float(row.get("odds"))
    probability = _float(row.get("model_probability"))
    if kickoff is None or odds <= 1.0 or probability <= 0.0:
        return None
    raw = row.get("raw_decision_snapshot")
    raw = raw if isinstance(raw, dict) else {}
    evidence = _evidence(row)
    source_summary = dict(raw.get("source_summary") or {}) if isinstance(raw.get("source_summary"), dict) else {}
    source_summary.update(
        {
            "focused_alpha": True,
            "focused_alpha_decision_key": row.get("decision_key"),
            "tracking_mode": "focused_alpha_accumulation",
            "tracking_reason": "focused_alpha_accumulation",
            "odds_sources": evidence["odds_sources"],
            "context_sources": evidence["context_sources"],
            "bookmakers": evidence["bookmakers"],
            "xg_sources": evidence["xg_sources"],
            "telegram_publication_enabled": False,
            "publication_contract_relaxed": False,
        }
    )
    diagnostics = dict(raw.get("diagnostics") or {}) if isinstance(raw.get("diagnostics"), dict) else {}
    diagnostics["focused_alpha_accumulation"] = {
        "decision_key": row.get("decision_key"),
        "observation_only": True,
        "never_publish": True,
        "blockers": list(row.get("blockers") or []),
    }
    market_probability = _float(row.get("market_probability"), 1.0 / odds)
    selection_key = _selection(row.get("selection_key") or row.get("selection"))
    family = _norm(row.get("family"))
    sport = _norm(raw.get("sport_key")) or "soccer"
    if sport not in {"soccer", "basketball", "baseball", "icehockey"}:
        sport = "soccer"
    return CandidateBet(
        match_key=_text(row.get("match_key")),
        sport_key=sport,
        league_name=_text(row.get("league_name")),
        home_team=_text(row.get("home_team")),
        away_team=_text(row.get("away_team")),
        commence_time=kickoff,
        family=family,
        selection=_text(row.get("selection")) or selection_key,
        selection_key=selection_key,
        odds=odds,
        fair_odds=1.0 / probability,
        implied_probability=1.0 / odds,
        market_probability=market_probability,
        consensus_probability=market_probability,
        model_probability=probability,
        final_probability=probability,
        adjusted_probability=probability,
        edge_pct=_float(row.get("edge_pp")),
        ev_pct=_float(row.get("ev_pct")),
        confidence=_float(row.get("confidence")),
        books_count=_int(row.get("books_count")),
        sources_count=max(_int(row.get("odds_sources_count")), len(evidence["odds_sources"])),
        model_mode="focused_alpha_accumulation_shadow",
        point=_float(row.get("point"), float("nan")) if row.get("point") not in (None, "") else None,
        expected_home=_float(raw.get("expected_home"), float("nan")) if raw.get("expected_home") not in (None, "") else None,
        expected_away=_float(raw.get("expected_away"), float("nan")) if raw.get("expected_away") not in (None, "") else None,
        reasons=["focused_alpha_accumulation_shadow", "observation_only_never_publish"],
        source_summary=source_summary,
        bookmaker=evidence["bookmakers"][0] if evidence["bookmakers"] else None,
        diagnostics=diagnostics,
        analysis={"focused_alpha_accumulation": True},
        publication_score=_float(row.get("risk_adjusted_utility")),
        selected_odds=odds,
        selected_implied_probability=1.0 / odds,
        canonical_adjusted_probability=probability,
        integrity_status=_text(raw.get("integrity_status")) or "unknown",
        integrity_reasons=list(raw.get("integrity_reasons") or []),
        integrity_report=dict(raw.get("integrity_report") or {}) if isinstance(raw.get("integrity_report"), dict) else {},
        raw_bucket_offers=list(raw.get("raw_bucket_offers") or []),
    )


def _persist_shadow(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if _norm(os.getenv("APP_ENV")) != "production":
        return {"status": "skipped_non_production", "added": 0}
    if not (os.getenv("GITHUB_RUN_ID") or os.getenv("HARIZON_FORCE_ACCUMULATION_STATE")):
        return {"status": "skipped_no_run_identity", "added": 0}
    try:
        from app.config import get_settings
        from app.state import JsonStateStore

        settings = get_settings()
        store = JsonStateStore(str(settings.state_path), str(settings.debug_path))
        candidates = [candidate for row in rows if (candidate := _candidate_bet(row)) is not None]
        added = store.store_shadow_candidates(
            candidates,
            tracking_reason="focused_alpha_accumulation",
        )
        return {
            "status": "ok",
            "candidates": len(candidates),
            "added": added,
            "state_path": str(settings.state_path),
            "telegram_publication_enabled": False,
            "publication_contract_relaxed": False,
        }
    except Exception as exc:
        return {
            "status": "error",
            "error": f"{type(exc).__name__}: {exc}",
            "added": 0,
            "publication_contract_relaxed": False,
        }


def _runtime_shadow_rows() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    try:
        from app.config import get_settings

        settings = get_settings()
        state = _load(Path(settings.state_path), {})
        rows = state.get("shadow_bets") if isinstance(state, dict) else []
        rows = [dict(row) for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []
        focused = []
        for row in rows:
            summary = row.get("source_summary") if isinstance(row.get("source_summary"), dict) else {}
            if (
                _text(row.get("tracking_reason")) == "focused_alpha_accumulation"
                or _text(summary.get("tracking_reason")) == "focused_alpha_accumulation"
                or _text(summary.get("tracking_mode")) == "focused_alpha_accumulation"
            ):
                focused.append(row)
        return focused, {
            "status": "ok",
            "state_path": str(settings.state_path),
            "focused_shadow_rows": len(focused),
            "settled_focused_shadow_rows": sum(
                _text(row.get("status")).lower() in _FINAL_RESULTS for row in focused
            ),
        }
    except Exception as exc:
        return [], {
            "status": "error",
            "error": f"{type(exc).__name__}: {exc}",
            "focused_shadow_rows": 0,
            "settled_focused_shadow_rows": 0,
        }


def _sync_results(
    selections: dict[str, dict[str, Any]],
    runtime_rows: list[dict[str, Any]],
) -> None:
    by_key: dict[str, dict[str, Any]] = {}
    for row in runtime_rows:
        summary = row.get("source_summary") if isinstance(row.get("source_summary"), dict) else {}
        diagnostics = row.get("diagnostics") if isinstance(row.get("diagnostics"), dict) else {}
        key = _text(
            summary.get("focused_alpha_decision_key")
            or _dict(diagnostics, "focused_alpha_accumulation").get("decision_key")
        )
        if key:
            by_key[key] = row
    for key, selection in selections.items():
        row = by_key.get(key)
        if not row:
            continue
        outcome = _text(row.get("status")).lower()
        if outcome not in _FINAL_RESULTS:
            continue
        selection["result"] = outcome
        selection["settled"] = True
        selection["settlement"] = row.get("settlement")
        selection["flat_unit_pnl"] = _unit_pnl(outcome, _float(selection.get("odds")))


def _refresh_closing(
    selections: dict[str, dict[str, Any]],
    observations: list[dict[str, Any]],
    as_of: datetime,
) -> None:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in observations:
        if isinstance(row, dict):
            grouped.setdefault(_text(row.get("decision_key")), []).append(row)
    for key, selection in selections.items():
        kickoff = _dt(selection.get("kickoff_utc"))
        rows = sorted(grouped.get(key, []), key=lambda row: _text(row.get("snapshot_at_utc")))
        pre_kickoff = [
            row
            for row in rows
            if (_dt(row.get("snapshot_at_utc")) is not None)
            and (kickoff is None or _dt(row.get("snapshot_at_utc")) <= kickoff)
            and _float(row.get("odds")) > 1.0
        ]
        closing = pre_kickoff[-1] if pre_kickoff else None
        selection["snapshots"] = len(rows)
        selection["latest_snapshot_at_utc"] = rows[-1].get("snapshot_at_utc") if rows else None
        selection["closing_odds_candidate"] = closing.get("odds") if closing else None
        selection["closing_snapshot_at_utc"] = closing.get("snapshot_at_utc") if closing else None
        finalized = bool(kickoff is not None and kickoff <= as_of)
        selection["closing_price_finalized"] = finalized
        taken = _float(selection.get("odds"))
        close = _float(selection.get("closing_odds_candidate"))
        selection["clv_pct"] = (
            round((taken / close - 1.0) * 100.0, 4)
            if finalized and taken > 1.0 and close > 1.0
            else None
        )


def _summary(selections: dict[str, dict[str, Any]], observations: list[dict[str, Any]]) -> dict[str, Any]:
    rows = list(selections.values())
    settled = [row for row in rows if row.get("settled")]
    wins = sum(_text(row.get("result")).lower() in {"won", "half_won"} for row in settled)
    losses = sum(_text(row.get("result")).lower() in {"lost", "half_lost"} for row in settled)
    pushes = len(settled) - wins - losses
    profit = round(sum(_float(row.get("flat_unit_pnl")) for row in settled), 4)
    risk = wins + losses
    with_clv = [row for row in rows if row.get("clv_pct") is not None]
    return {
        "selected_decisions": len(rows),
        "observations": len(observations),
        "settled": len(settled),
        "won_or_half_won": wins,
        "lost_or_half_lost": losses,
        "push_or_void": pushes,
        "profit_units_flat_1u": profit,
        "hit_rate_ex_push_pct": round(100.0 * wins / risk, 3) if risk else None,
        "yield_pct_flat_1u": round(100.0 * profit / risk, 3) if risk else None,
        "decisions_with_clv": len(with_clv),
        "mean_clv_pct": (
            round(sum(_float(row.get("clv_pct")) for row in with_clv) / len(with_clv), 4)
            if with_clv
            else None
        ),
    }


def accumulate(board: dict[str, Any] | None = None) -> dict[str, Any]:
    try:
        from app.services import focused_alpha_learning_ledger as ledger

        payload = board if isinstance(board, dict) else _load(ledger.BOARD_PATH, {})
    except Exception:
        payload = board if isinstance(board, dict) else {}
    ranked = [dict(row) for row in (payload.get("ranked") or []) if isinstance(row, dict)]
    strict = [dict(row) for row in (payload.get("selected_shadow") or []) if isinstance(row, dict)]
    strict_keys = {_text(row.get("decision_key")) for row in strict if _text(row.get("decision_key"))}
    snapshot_at = _text(payload.get("created_at_utc")) or datetime.now(UTC).isoformat()
    run_id = _text(os.getenv("GITHUB_RUN_ID") or os.getenv("HARIZON_RUN_ID") or f"local:{snapshot_at}")

    previous = _load(ACCUMULATION_PATH, {})
    selections = {
        key: dict(value)
        for key, value in (previous.get("selections") or {}).items()
        if isinstance(value, dict)
    } if isinstance(previous, dict) and isinstance(previous.get("selections"), dict) else {}
    observations = [
        dict(row)
        for row in (previous.get("observations") or [])
        if isinstance(row, dict)
    ] if isinstance(previous, dict) else []

    chosen, rejection_counts, limits = _choose(ranked, selections, strict_keys, snapshot_at)
    for row in chosen:
        key = _text(row.get("decision_key"))
        selections[key] = _selection_record(row, snapshot_at)

    tracked = set(selections)
    by_id = {
        _text(row.get("observation_id")): row
        for row in observations
        if _text(row.get("observation_id"))
    }
    for row in ranked:
        if _text(row.get("decision_key")) not in tracked:
            continue
        observation = _observation(row, snapshot_at, run_id)
        by_id[observation["observation_id"]] = observation
    observations = sorted(by_id.values(), key=lambda row: _text(row.get("snapshot_at_utc")))[-5000:]

    persistence = _persist_shadow(chosen)
    runtime_rows, runtime_report = _runtime_shadow_rows()
    _sync_results(selections, runtime_rows)
    _refresh_closing(selections, observations, datetime.now(UTC))
    summary = _summary(selections, observations)
    result = {
        "status": "ok",
        "updated_at_utc": datetime.now(UTC).isoformat(),
        "mode": "bounded_observation_only_settlement_backed",
        "policy": "select_max_two_daily_near_misses_track_price_and_settle_via_shadow_bets",
        "selected_this_run": len(chosen),
        "selected_keys_this_run": [_text(row.get("decision_key")) for row in chosen],
        "strict_selected_this_run": len(strict_keys),
        "rejection_counts": rejection_counts,
        "limits": limits,
        "runtime_shadow_persistence": persistence,
        "runtime_shadow_source": runtime_report,
        "summary": summary,
        "selections": selections,
        "observations": observations,
        "telegram_publication_enabled": False,
        "publication_contract_relaxed": False,
    }
    _write(ACCUMULATION_PATH, result)
    return result


def install() -> dict[str, Any]:
    global _INSTALLED
    if _INSTALLED:
        return {"status": "already_installed"}
    try:
        from app.services import focused_alpha_learning_ledger as target
    except Exception as exc:
        return {"status": "import_error", "error": f"{type(exc).__name__}: {exc}"}
    original = target.update_learning_ledger
    if getattr(original, "_focused_alpha_accumulation_patch", False):
        _INSTALLED = True
        return {"status": "already_installed"}

    def wrapped(board: dict[str, Any] | None = None) -> dict[str, Any]:
        result = original(board)
        accumulation = accumulate(board)
        if isinstance(result, dict):
            result["accumulation"] = {
                "status": accumulation.get("status"),
                "mode": accumulation.get("mode"),
                "selected_this_run": accumulation.get("selected_this_run"),
                "summary": accumulation.get("summary"),
                "runtime_shadow_persistence": accumulation.get("runtime_shadow_persistence"),
                "artifact": str(ACCUMULATION_PATH.relative_to(ROOT)),
                "telegram_publication_enabled": False,
                "publication_contract_relaxed": False,
            }
            summary = result.setdefault("summary", {})
            if isinstance(summary, dict):
                summary["accumulation_shadow_selected_this_run"] = accumulation.get("selected_this_run", 0)
                summary["accumulation_shadow_selected_total"] = _dict(accumulation, "summary").get("selected_decisions", 0)
                summary["accumulation_shadow_settled"] = _dict(accumulation, "summary").get("settled", 0)
                summary["accumulation_shadow_profit_units"] = _dict(accumulation, "summary").get("profit_units_flat_1u", 0.0)
            with suppress(Exception):
                target._write(target.LEDGER_PATH, result)
        return result

    wrapped._focused_alpha_accumulation_patch = True  # type: ignore[attr-defined]
    wrapped._focused_alpha_original = original  # type: ignore[attr-defined]
    target.update_learning_ledger = wrapped
    _INSTALLED = True
    return {
        "status": "installed",
        "artifact": str(ACCUMULATION_PATH.relative_to(ROOT)),
        "publication_contract_relaxed": False,
        "telegram_publication_enabled": False,
    }


__all__ = ["ACCUMULATION_PATH", "accumulate", "install"]
