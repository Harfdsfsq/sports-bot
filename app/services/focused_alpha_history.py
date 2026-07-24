"""Canonical historical audit for the Focused Alpha pipeline.

The repository contains overlapping publication exports. This module merges them by
prediction/semantic identity, prefers the most complete settled representation,
recomputes flat-unit PnL from result and price, and exposes conservative league
priors. Missing probabilities, closing prices or source identity keep the dataset
out of live-learning mode.
"""

from __future__ import annotations

import csv
import io
import json
import math
import re
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / ".data"
EXPORT = DATA / "exports"
REPORT_PATH = EXPORT / "latest-focused-alpha-history-audit.json"
CANONICAL_PATH = EXPORT / "latest-focused-alpha-canonical-history.json"

_FINAL_RESULTS = {"won", "lost", "push", "void", "cancelled", "refunded"}
_EMPTY_TEXT = {"", "null", "none", "nan", "n/a", "unknown"}


def _load_json(path: Path, default: Any) -> Any:
    try:
        value = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        return value if value is not None else default
    except Exception:
        return default


def _write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _text(value: Any) -> str:
    return str(value or "").strip()


def _norm(value: Any) -> str:
    text = _text(value).lower().replace("ё", "е")
    text = re.sub(r"[^a-z0-9а-я]+", " ", text)
    return " ".join(text.split())


def _is_empty(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip().lower() in _EMPTY_TEXT
    if isinstance(value, (list, tuple, set, dict)):
        return len(value) == 0
    return False


def _float(value: Any) -> float | None:
    if _is_empty(value):
        return None
    try:
        result = float(str(value).replace(",", "."))
        return result if math.isfinite(result) else None
    except Exception:
        return None


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return _text(value).lower() in {"1", "true", "yes", "on"}


def _result(row: dict[str, Any]) -> str:
    for key in ("result", "status", "settlement_result"):
        value = _text(row.get(key)).lower()
        if value in _FINAL_RESULTS or value == "pending":
            return value
    hit = row.get("is_hit")
    if hit is True or _text(hit).lower() == "true":
        return "won"
    if hit is False or _text(hit).lower() == "false":
        return "lost"
    return "pending"


def _semantic_key(row: dict[str, Any]) -> str:
    direct = _text(
        row.get("canonical_publication_key")
        or row.get("prediction_id")
        or row.get("fingerprint")
    )
    if direct and direct != "||||":
        return direct
    match = _norm(row.get("canonical_match_id") or row.get("match_key"))
    if not match:
        home = _norm(row.get("home_team") or row.get("home"))
        away = _norm(row.get("away_team") or row.get("away"))
        kickoff = _text(
            row.get("commence_time_utc")
            or row.get("commence_time")
            or row.get("kickoff_utc")
        )[:10]
        match = f"{kickoff}|{home}|{away}"
    family = _norm(row.get("family") or row.get("market_family"))
    selection = _norm(row.get("selection_key") or row.get("selection"))
    point = _float(row.get("point") or row.get("line") or row.get("handicap"))
    point_text = "" if point is None else f"{point:g}"
    return "|".join((match, family, selection, point_text))


def _csv_rows(path: Path) -> list[dict[str, Any]]:
    try:
        text = path.read_text(encoding="utf-8-sig", errors="replace")
        return [dict(row) for row in csv.DictReader(io.StringIO(text))]
    except Exception:
        return []


def _json_rows(path: Path) -> list[dict[str, Any]]:
    payload = _load_json(path, [])
    if isinstance(payload, list):
        return [dict(row) for row in payload if isinstance(row, dict)]
    if not isinstance(payload, dict):
        return []
    rows: list[dict[str, Any]] = []
    for value in payload.values():
        if isinstance(value, dict):
            rows.append(dict(value))
    for key in ("rows", "bets", "items", "published_candidates"):
        value = payload.get(key)
        if isinstance(value, list):
            rows.extend(dict(row) for row in value if isinstance(row, dict))
    return rows


def collect_raw_rows() -> list[dict[str, Any]]:
    sources = (
        (EXPORT / "latest-bets.csv", _csv_rows),
        (EXPORT / "latest-bets.json", _json_rows),
        (DATA / "fallback-sent-index.json", _json_rows),
        (DATA / "published-candidate-index.json", _json_rows),
    )
    rows: list[dict[str, Any]] = []
    for path, loader in sources:
        for source_row in loader(path):
            row = dict(source_row)
            row["_history_source_path"] = str(path.relative_to(ROOT))
            rows.append(row)
    return rows


def _completeness(row: dict[str, Any]) -> tuple[int, int, int, int]:
    final = int(_result(row) in _FINAL_RESULTS)
    settled = int(
        bool(_text(row.get("settled_at")))
        or _text(row.get("settlement_attempt_reason")).lower() == "settled"
    )
    useful_fields = sum(
        not _is_empty(row.get(key))
        for key in (
            "odds",
            "stake_amount",
            "model_probability_pct",
            "adjusted_probability_pct",
            "market_probability_pct",
            "edge_pct",
            "ev_pct",
            "quality_score",
            "confidence",
            "odds_sources",
            "context_sources",
            "selected_bookmaker",
            "published_at",
            "commence_time_utc",
            "final_score",
        )
    )
    timestamp = _text(
        row.get("settled_at") or row.get("published_at") or row.get("sent_at")
    )
    return final, settled, useful_fields, int(bool(timestamp))


def _merge_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    source_paths: set[str] = set()
    for row in sorted(rows, key=_completeness):
        source_paths.add(_text(row.get("_history_source_path")))
        for key, value in row.items():
            if key.startswith("_history_"):
                continue
            if not _is_empty(value):
                merged[key] = value
    merged["history_source_paths"] = sorted(path for path in source_paths if path)
    merged["history_duplicate_rows"] = len(rows)
    merged["history_key"] = _semantic_key(merged)
    merged["result"] = _result(merged)
    merged["telegram_sent"] = any(_bool(row.get("telegram_sent")) for row in rows)
    return merged


def canonical_history(
    raw_rows: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    ignored = 0
    for row in raw_rows if raw_rows is not None else collect_raw_rows():
        key = _semantic_key(row)
        if not key.strip("|") or key == "||||":
            ignored += 1
            continue
        grouped[key].append(row)
    rows = [_merge_rows(group) for group in grouped.values()]
    rows.sort(
        key=lambda row: (
            _text(row.get("commence_time_utc") or row.get("commence_time")),
            row["history_key"],
        )
    )
    for row in rows:
        row["history_rows_ignored_without_identity"] = ignored
    return rows


def _unit_pnl(row: dict[str, Any]) -> float | None:
    result = _result(row)
    price = _float(row.get("odds"))
    if result == "won" and price and price > 1.0:
        return price - 1.0
    if result == "lost":
        return -1.0
    if result in {"push", "void", "cancelled", "refunded"}:
        return 0.0
    return None


def _odds_band(value: float | None) -> str:
    if value is None:
        return "missing"
    if value < 1.70:
        return "lt_1_70"
    if value < 1.90:
        return "1_70_1_89"
    if value < 2.20:
        return "1_90_2_19"
    if value < 2.60:
        return "2_20_2_59"
    return "2_60_plus"


def _group_stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    settled_pairs = [(row, _unit_pnl(row)) for row in rows]
    settled = [(row, pnl) for row, pnl in settled_pairs if pnl is not None]
    wins = sum(_result(row) == "won" for row, _ in settled)
    losses = sum(_result(row) == "lost" for row, _ in settled)
    pushes = len(settled) - wins - losses
    units = round(sum(float(pnl) for _, pnl in settled), 4)
    risk = sum(_result(row) in {"won", "lost"} for row, _ in settled)
    yield_pct = round(100.0 * units / risk, 3) if risk else None
    return {
        "rows": len(rows),
        "settled": len(settled),
        "won": wins,
        "lost": losses,
        "push_or_void": pushes,
        "hit_rate_ex_push_pct": (
            round(100.0 * wins / (wins + losses), 3) if wins + losses else None
        ),
        "profit_units_flat_1u": units,
        "yield_pct_flat_1u": yield_pct,
    }


def _nested_stats(rows: list[dict[str, Any]], key_fn: Any) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[str(key_fn(row) or "unknown")].append(row)
    return {key: _group_stats(value) for key, value in sorted(groups.items())}


def _has_probability(row: dict[str, Any]) -> bool:
    return any(
        _float(row.get(key)) is not None
        for key in ("model_probability_pct", "adjusted_probability_pct")
    )


def _has_closing_price(row: dict[str, Any]) -> bool:
    return any(
        _float(row.get(key)) is not None
        for key in (
            "closing_odds",
            "close_odds",
            "closing_price",
            "market_close_odds",
        )
    )


def _source_names(value: Any) -> set[str]:
    raw = value if isinstance(value, (list, tuple, set)) else re.split(
        r"[,;+|/]", _text(value)
    )
    return {_norm(item) for item in raw if _norm(item)}


def _has_source_identity(row: dict[str, Any]) -> bool:
    odds = set()
    contexts = set()
    for key in ("odds_sources", "line_sources", "selected_source"):
        odds.update(_source_names(row.get(key)))
    for key in ("context_sources", "confirmation_sources"):
        contexts.update(_source_names(row.get(key)))
    return bool(odds) and bool(contexts)


def build_history_audit() -> dict[str, Any]:
    raw_rows = collect_raw_rows()
    rows = canonical_history(raw_rows)
    sent = [
        row
        for row in rows
        if _bool(row.get("telegram_sent"))
        or _text(row.get("publication_lifecycle_status")) == "telegram_sent"
    ]
    settled = [row for row in sent if _unit_pnl(row) is not None]
    pending_with_kickoff = [
        row
        for row in sent
        if _result(row) == "pending"
        and _text(row.get("commence_time_utc") or row.get("commence_time"))[:10]
    ]
    with_probability = sum(_has_probability(row) for row in settled)
    with_closing = sum(_has_closing_price(row) for row in settled)
    with_source_identity = sum(_has_source_identity(row) for row in settled)
    minimum_settled = 100
    probability_required = math.ceil(0.9 * len(settled))
    closing_required = math.ceil(0.8 * len(settled))
    identity_required = math.ceil(0.9 * len(settled))
    live_learning_ready = (
        len(settled) >= minimum_settled
        and with_probability >= probability_required
        and with_closing >= closing_required
        and with_source_identity >= identity_required
    )
    blockers: list[str | None] = []
    if not live_learning_ready:
        blockers = [
            (
                f"settled_sample_below_min:{len(settled)}/{minimum_settled}"
                if len(settled) < minimum_settled
                else None
            ),
            (
                f"model_probability_coverage:{with_probability}/{len(settled)}"
                if settled and with_probability < probability_required
                else None
            ),
            (
                f"closing_price_coverage:{with_closing}/{len(settled)}"
                if settled and with_closing < closing_required
                else None
            ),
            (
                f"source_identity_coverage:{with_source_identity}/{len(settled)}"
                if settled and with_source_identity < identity_required
                else None
            ),
        ]
    report = {
        "status": "ok",
        "created_at_utc": datetime.now(UTC).isoformat(),
        "policy": "canonical_semantic_dedupe_prefer_settled_recompute_flat_unit_pnl",
        "raw_rows": len(raw_rows),
        "canonical_rows": len(rows),
        "telegram_sent_rows": len(sent),
        "settled_rows": len(settled),
        "pending_rows": len(sent) - len(settled),
        "pending_with_known_kickoff": len(pending_with_kickoff),
        "duplicates_collapsed": max(0, len(raw_rows) - len(rows)),
        "performance": _group_stats(sent),
        "by_league": _nested_stats(
            sent, lambda row: _text(row.get("league_name")) or "unknown"
        ),
        "by_selection": _nested_stats(
            sent,
            lambda row: _text(row.get("selection_key") or row.get("selection"))
            or "unknown",
        ),
        "by_odds_band": _nested_stats(
            sent, lambda row: _odds_band(_float(row.get("odds")))
        ),
        "data_quality": {
            "settled_with_model_probability": with_probability,
            "settled_with_closing_price": with_closing,
            "settled_with_odds_and_context_identity": with_source_identity,
            "settled_missing_model_probability": len(settled) - with_probability,
            "settled_missing_closing_price": len(settled) - with_closing,
            "settled_missing_source_identity": len(settled) - with_source_identity,
            "pnl_recomputed_from_result_price": True,
            "legacy_books_count_not_trusted_as_independent_sources": True,
        },
        "live_learning_ready": live_learning_ready,
        "live_learning_blockers": [value for value in blockers if value],
        "publication_thresholds_auto_tuned": False,
    }
    _write(CANONICAL_PATH, rows)
    _write(REPORT_PATH, report)
    return report


def league_prior(
    league_name: Any,
    report: dict[str, Any] | None = None,
) -> dict[str, float]:
    audit = report or _load_json(REPORT_PATH, {})
    row = (
        (audit.get("by_league") or {}).get(_text(league_name))
        if isinstance(audit, dict)
        else None
    )
    if not isinstance(row, dict):
        return {"sample": 0.0, "reliability": 0.0, "profit_signal": 0.0}
    sample = float(row.get("settled") or 0)
    yield_pct = _float(row.get("yield_pct_flat_1u")) or 0.0
    shrink = sample / (sample + 30.0)
    reliability = min(1.0, sample / 20.0)
    profit_signal = max(-1.0, min(1.0, yield_pct / 20.0)) * shrink
    if not bool(audit.get("live_learning_ready")):
        profit_signal *= 0.15
    return {
        "sample": sample,
        "reliability": reliability,
        "profit_signal": profit_signal,
    }


__all__ = [
    "CANONICAL_PATH",
    "REPORT_PATH",
    "build_history_audit",
    "canonical_history",
    "collect_raw_rows",
    "league_prior",
]
