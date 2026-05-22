from __future__ import annotations

"""Candidate value/ranking runtime patch.

Live runs showed a systematic issue: candidates were selected before quality by
raw model_probability/publication_score, then rejected later because the
canonical value check uses adjusted_probability against the actual selected odds.
That produced reports full of negative-EV candidates.

This patch does not loosen publication quality. It changes candidate ordering and
pre-quality filtering so the factory surfaces calibrated positive value first.
It also wraps build_candidates as a final safety net: even if another runtime
wrapper bypasses _filter_and_rank, negative canonical-EV candidates are removed
before quality/reserve fallback.
"""

import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

UTC = timezone.utc
ROOT = Path(__file__).resolve().parents[2]
REPORT_PATH = ROOT / ".data" / "exports" / "latest-candidate-value-runtime-patch.json"
_INSTALLED = False


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        number = float(str(value).replace(",", "."))
        if math.isfinite(number):
            return number
    except Exception:
        pass
    return default


def _as_int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ""):
            return default
        return int(float(str(value)))
    except Exception:
        return default


def _truthy(value: Any, default: bool = False) -> bool:
    raw = str(value if value is not None else "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on", "force"}


def _write_report(payload: dict[str, Any]) -> None:
    try:
        REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        REPORT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except Exception:
        pass


def _selected_odds(candidate: Any) -> float:
    return _as_float(getattr(candidate, "selected_odds", None), 0.0) or _as_float(getattr(candidate, "odds", None), 0.0)


def _quality_final_probability(candidate: Any) -> float:
    """Return the latest post-quality calibrated probability when available.

    Earlier runtime patches stored canonical_adjusted_probability before the
    quality/historical calibration stage. In live runs this made pre-quality
    rows look positive while Telegram fallback later rejected them as negative
    after final_adjusted_probability was applied. Prefer the post-quality value
    from diagnostics.quality whenever it exists.
    """
    try:
        diag = getattr(candidate, "diagnostics", None)
        if isinstance(diag, dict):
            quality = diag.get("quality")
            if isinstance(quality, dict):
                for key in ("final_adjusted_probability", "final_probability", "probability_used_for_ev"):
                    value = _as_float(quality.get(key), 0.0)
                    if value > 0:
                        return value
    except Exception:
        pass
    return 0.0


def _adjusted_probability(candidate: Any) -> float:
    post_quality = _quality_final_probability(candidate)
    if post_quality > 0:
        return post_quality
    return (
        _as_float(getattr(candidate, "canonical_adjusted_probability", None), 0.0)
        or _as_float(getattr(candidate, "probability_used_for_ev", None), 0.0)
        or _as_float(getattr(candidate, "adjusted_probability", None), 0.0)
        or _as_float(getattr(candidate, "final_probability", None), 0.0)
        or _as_float(getattr(candidate, "model_probability", None), 0.0)
    )


def _selected_implied(candidate: Any) -> float:
    explicit = _as_float(getattr(candidate, "selected_implied_probability", None), 0.0)
    if explicit > 0:
        return explicit
    odds = _selected_odds(candidate)
    return 1.0 / odds if odds > 1.0 else _as_float(getattr(candidate, "implied_probability", None), 0.0)


def _canonical_metrics(candidate: Any) -> dict[str, float]:
    odds = _selected_odds(candidate)
    probability = _adjusted_probability(candidate)
    implied = _selected_implied(candidate)
    ev_pct = (probability * odds - 1.0) * 100.0 if odds > 1.0 and probability > 0 else -999.0
    edge_pp = (probability - implied) * 100.0 if probability > 0 and implied > 0 else -999.0
    return {
        "odds": odds,
        "probability": probability,
        "implied": implied,
        "canonical_ev_pct": ev_pct,
        "canonical_edge_pp": edge_pp,
        "raw_ev_pct": _as_float(getattr(candidate, "ev_pct", None), 0.0),
        "raw_edge_pct": _as_float(getattr(candidate, "edge_pct", None), 0.0),
    }


def _annotate_candidate(candidate: Any) -> dict[str, float]:
    metrics = _canonical_metrics(candidate)
    try:
        candidate.canonical_adjusted_probability = metrics["probability"]
        candidate.selected_odds = metrics["odds"]
        candidate.selected_implied_probability = metrics["implied"]
        candidate.probability_used_for_ev = metrics["probability"]
        candidate.price_used_for_ev = metrics["odds"]
        diag = dict(getattr(candidate, "diagnostics", {}) or {})
        diag["prequality_canonical_value"] = metrics
        candidate.diagnostics = diag
        summary = dict(getattr(candidate, "source_summary", {}) or {})
        summary["prequality_canonical_ev_pct"] = round(metrics["canonical_ev_pct"], 4)
        summary["prequality_canonical_edge_pp"] = round(metrics["canonical_edge_pp"], 4)
        candidate.source_summary = summary
    except Exception:
        pass
    return metrics


def _is_positive_value(candidate: Any) -> bool:
    min_ev = _as_float(os.getenv("PREQUALITY_CANONICAL_MIN_EV_PCT"), 0.0)
    min_edge = _as_float(os.getenv("PREQUALITY_CANONICAL_MIN_EDGE_PP"), 0.0)
    metrics = _canonical_metrics(candidate)
    return metrics["canonical_ev_pct"] >= min_ev and metrics["canonical_edge_pp"] >= min_edge


def _rank_tuple(candidate: Any, original_value: tuple[Any, ...] = ()) -> tuple[Any, ...]:
    metrics = _annotate_candidate(candidate)
    return (
        metrics["canonical_ev_pct"],
        metrics["canonical_edge_pp"],
        _as_float(getattr(candidate, "confidence", None), 0.0),
        _as_int(getattr(candidate, "sources_count", None), 0),
        _as_int(getattr(candidate, "books_count", None), 0),
        _as_float(getattr(candidate, "publication_score", None), 0.0),
        *original_value,
    )


def _filter_positive_value_rows(rows: list[Any], rejections: dict[str, int] | None = None) -> tuple[list[Any], int]:
    hard_filter = _truthy(os.getenv("PREQUALITY_CANONICAL_VALUE_FILTER_ENABLED"), True)
    output: list[Any] = []
    negative = 0
    for item in rows:
        _annotate_candidate(item)
        if hard_filter and not _is_positive_value(item):
            negative += 1
            continue
        output.append(item)
    if negative and isinstance(rejections, dict):
        rejections["prequality_canonical_value_guard"] = int(rejections.get("prequality_canonical_value_guard", 0) or 0) + negative
    return output, negative


def _source_count_from_offers(offers: Any) -> int:
    values: set[str] = set()
    for offer in offers or []:
        src = offer.get("source") if isinstance(offer, dict) else getattr(offer, "source", None)
        text = str(src or "").strip().lower()
        if text:
            values.add(text)
    return len(values)


def _book_count_from_offers(offers: Any, normalizer) -> int:
    values: set[str] = set()
    for offer in offers or []:
        book = offer.get("bookmaker") if isinstance(offer, dict) else getattr(offer, "bookmaker", None)
        try:
            text = normalizer(str(book or ""))
        except Exception:
            text = str(book or "").strip().lower()
        if text:
            values.add(text)
    return len(values)


def _patch_candidate_factory() -> dict[str, Any]:
    from app.services.model import CandidateFactory

    if getattr(CandidateFactory, "_harizon_candidate_value_patch", False):
        return {"candidate_factory": "already_installed"}

    original_build = getattr(CandidateFactory, "build_candidates", None)
    original_rank = getattr(CandidateFactory, "_candidate_rank_key", None)
    original_filter = getattr(CandidateFactory, "_filter_and_rank", None)
    original_signal_ready = getattr(CandidateFactory, "_market_signal_ready_for_derived", None)

    def candidate_rank_key_value_first(self: Any, candidate: Any):
        original_value: tuple[Any, ...] = ()
        if callable(original_rank):
            try:
                value = original_rank(self, candidate)
                original_value = value if isinstance(value, tuple) else (value,)
            except Exception:
                original_value = ()
        return _rank_tuple(candidate, original_value)

    def filter_and_rank_value_first(self: Any, candidates: list[Any], rejections: dict[str, int]):
        rows = list(candidates or [])
        rows.sort(key=lambda item: candidate_rank_key_value_first(self, item), reverse=True)
        positive, negative_count = _filter_positive_value_rows(rows, rejections)
        if callable(original_filter):
            result = list(original_filter(self, positive, rejections) or [])
        else:
            result = positive
        # Final safety after original filter because original filter can reuse raw
        # ev_pct/publication_score and reintroduce an ordering not based on canonical EV.
        result, negative_after_original = _filter_positive_value_rows(list(result), rejections)
        result.sort(key=lambda item: candidate_rank_key_value_first(self, item), reverse=True)
        limit = max(1, _as_int(getattr(self.settings, "max_internal_candidates_per_run", None), 8))
        trimmed = result[:limit]
        _write_report({
            "created_at_utc": datetime.now(UTC).isoformat(),
            "stage": "filter_and_rank",
            "input_candidates": len(rows),
            "prequality_negative_filtered": negative_count,
            "negative_after_original_filter": negative_after_original,
            "after_original_filter": len(result),
            "returned": len(trimmed),
            "min_ev_pct": _as_float(os.getenv("PREQUALITY_CANONICAL_MIN_EV_PCT"), 0.0),
            "min_edge_pp": _as_float(os.getenv("PREQUALITY_CANONICAL_MIN_EDGE_PP"), 0.0),
            "hard_filter": _truthy(os.getenv("PREQUALITY_CANONICAL_VALUE_FILTER_ENABLED"), True),
            "sample": [
                {
                    "match_key": getattr(x, "match_key", ""),
                    "home": getattr(x, "home_team", ""),
                    "away": getattr(x, "away_team", ""),
                    "family": getattr(x, "family", ""),
                    "selection": getattr(x, "selection", ""),
                    **{k: round(v, 4) for k, v in _canonical_metrics(x).items()},
                }
                for x in trimmed[:20]
            ],
        })
        return trimmed

    def build_candidates_value_safety(self: Any, matches, offers_by_match, contexts_by_match, market_signals_by_match=None):  # type: ignore[no-untyped-def]
        if not callable(original_build):
            return [], {}, {"candidate_value_runtime_patch": {"error": "original_build_missing"}}
        candidates, rejections, debug = original_build(
            self,
            matches,
            offers_by_match,
            contexts_by_match,
            market_signals_by_match=market_signals_by_match,
        )
        rejections = dict(rejections or {})
        debug = dict(debug or {})
        rows = list(candidates or [])
        for item in rows:
            _annotate_candidate(item)
        rows.sort(key=lambda item: candidate_rank_key_value_first(self, item), reverse=True)
        positive, negative_count = _filter_positive_value_rows(rows, rejections)
        positive.sort(key=lambda item: candidate_rank_key_value_first(self, item), reverse=True)
        limit = max(1, _as_int(getattr(self.settings, "max_internal_candidates_per_run", None), 8))
        returned = positive[:limit]
        value_report = {
            "created_at_utc": datetime.now(UTC).isoformat(),
            "stage": "build_candidates_post_filter",
            "input_candidates": len(rows),
            "prequality_negative_filtered": negative_count,
            "returned": len(returned),
            "min_ev_pct": _as_float(os.getenv("PREQUALITY_CANONICAL_MIN_EV_PCT"), 0.0),
            "min_edge_pp": _as_float(os.getenv("PREQUALITY_CANONICAL_MIN_EDGE_PP"), 0.0),
            "hard_filter": _truthy(os.getenv("PREQUALITY_CANONICAL_VALUE_FILTER_ENABLED"), True),
            "sample": [
                {
                    "match_key": getattr(x, "match_key", ""),
                    "home": getattr(x, "home_team", ""),
                    "away": getattr(x, "away_team", ""),
                    "family": getattr(x, "family", ""),
                    "selection": getattr(x, "selection", ""),
                    **{k: round(v, 4) for k, v in _canonical_metrics(x).items()},
                }
                for x in returned[:20]
            ],
        }
        debug["candidate_value_runtime_patch"] = value_report
        _write_report(value_report)
        return returned, rejections, debug

    def market_signal_ready_with_consensus_relief(self: Any, family: str, market_signal: dict[str, Any] | None, offers: list[Any] | None):
        if callable(original_signal_ready):
            try:
                if original_signal_ready(self, family, market_signal, offers):
                    return True
            except Exception:
                pass
        if not isinstance(market_signal, dict):
            return False
        if str(family or "").lower() == "h2h" and str(market_signal.get("selection_key") or "").lower() == "draw":
            return False
        if not _truthy(os.getenv("MARKET_DERIVED_SINGLE_SNAPSHOT_CONSENSUS_ENABLED"), True):
            return False
        books_count = _as_int(market_signal.get("books_count"), 0) or _book_count_from_offers(offers, getattr(self, "_norm_book", lambda x: str(x).lower()))
        sources_count = _as_int(market_signal.get("sources_count"), 0) or _source_count_from_offers(offers)
        observation_count = _as_int(market_signal.get("observation_count"), 0)
        if observation_count <= 0 and (books_count >= 2 or sources_count >= 2):
            observation_count = 1
        min_books = max(1, _as_int(os.getenv("MARKET_DERIVED_CONSENSUS_RELIEF_MIN_BOOKS"), 2))
        min_sources = max(1, _as_int(os.getenv("MARKET_DERIVED_CONSENSUS_RELIEF_MIN_SOURCES"), 1))
        min_obs = max(1, _as_int(os.getenv("MARKET_DERIVED_CONSENSUS_RELIEF_MIN_OBSERVATIONS"), 1))
        if books_count < min_books or sources_count < min_sources or observation_count < min_obs:
            return False
        edge_pct = _as_float(market_signal.get("best_vs_consensus_edge_pct"), 0.0)
        if edge_pct < _as_float(os.getenv("MARKET_DERIVED_CONSENSUS_RELIEF_MIN_EDGE_PCT"), 2.1):
            return False
        dispersion = market_signal.get("consensus_dispersion_pct")
        if dispersion not in (None, "") and _as_float(dispersion, 999.0) > _as_float(os.getenv("MARKET_DERIVED_CONSENSUS_RELIEF_MAX_DISPERSION_PCT"), 4.8):
            return False
        try:
            market_signal["single_snapshot_consensus_relief"] = True
            market_signal["observation_count"] = max(_as_int(market_signal.get("observation_count"), 0), observation_count)
            market_signal["books_count"] = max(_as_int(market_signal.get("books_count"), 0), books_count)
            market_signal["sources_count"] = max(_as_int(market_signal.get("sources_count"), 0), sources_count)
        except Exception:
            pass
        return True

    CandidateFactory._candidate_rank_key = candidate_rank_key_value_first  # type: ignore[assignment]
    CandidateFactory._filter_and_rank = filter_and_rank_value_first  # type: ignore[assignment]
    CandidateFactory.build_candidates = build_candidates_value_safety  # type: ignore[assignment]
    CandidateFactory._market_signal_ready_for_derived = market_signal_ready_with_consensus_relief  # type: ignore[assignment]
    CandidateFactory._harizon_candidate_value_patch = True
    return {
        "candidate_factory": "patched",
        "rank_key": "canonical_ev_first",
        "prequality_filter": "canonical_ev_edge_non_negative",
        "build_candidates_post_filter": True,
        "market_derived_single_snapshot_consensus": True,
    }


def install() -> dict[str, Any]:
    global _INSTALLED
    if _INSTALLED:
        return {"status": "already_installed"}
    _INSTALLED = True
    payload = {"created_at_utc": datetime.now(UTC).isoformat(), "status": "starting"}
    try:
        payload.update(_patch_candidate_factory())
        payload["status"] = "installed"
    except Exception as exc:
        payload["status"] = "error"
        payload["error"] = f"{type(exc).__name__}: {exc}"
    _write_report(payload)
    return payload
