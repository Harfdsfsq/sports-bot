from __future__ import annotations

"""Production quality layer for controlled HARIZON testing.

Adds three things without disabling hard safety guards:
1) a real reserve_quality_score instead of q=0.0 for B-tier candidates;
2) a publication ledger snapshot for later ROI/CLV/result learning;
3) lightweight weather/league reliability modifiers from already available fields.
"""

import json
import math
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

EXPORT = Path(".data/exports")
LEDGER = Path(".data/prediction-quality-ledger.jsonl")
REPORT = EXPORT / "latest-production-quality-layer.json"

LOW_QUALITY_TOKENS = ("u19", "u21", "youth", "reserve", "friendly", "жен", "молод", "резерв")
CUP_VOLATILE_TOKENS = ("cup", "куб", "knockout", "кнок")


def _num(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        n = float(str(value).replace(",", "."))
        return n if math.isfinite(n) else default
    except Exception:
        return default


def _count(value: Any) -> int:
    try:
        if isinstance(value, (list, tuple, set, dict)):
            return len(value)
        if value in (None, ""):
            return 0
        return int(float(str(value).replace(",", ".")))
    except Exception:
        return 0


def _get(candidate: dict[str, Any], metrics: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if metrics.get(key) not in (None, ""):
            return metrics.get(key)
        if candidate.get(key) not in (None, ""):
            return candidate.get(key)
    summary = candidate.get("source_summary") if isinstance(candidate.get("source_summary"), dict) else {}
    for key in keys:
        if summary.get(key) not in (None, ""):
            return summary.get(key)
    return None


def league_reliability(candidate: dict[str, Any]) -> dict[str, Any]:
    league = str(candidate.get("league") or candidate.get("league_name") or candidate.get("competition") or "").lower()
    score = 64.0
    reasons: list[str] = []
    if any(t in league for t in LOW_QUALITY_TOKENS):
        score -= 22.0; reasons.append("low_quality_or_youth_segment")
    if any(t in league for t in CUP_VOLATILE_TOKENS):
        score -= 7.0; reasons.append("cup_or_knockout_volatility")
    if "romania" in league or "романиа" in league:
        score -= 2.0; reasons.append("medium_reliability_region")
    return {"score": max(20.0, min(85.0, score)), "reasons": reasons, "league": league}


def weather_modifier(candidate: dict[str, Any]) -> dict[str, Any]:
    weather = candidate.get("weather") if isinstance(candidate.get("weather"), dict) else {}
    ctx = candidate.get("context") if isinstance(candidate.get("context"), dict) else {}
    if not weather and isinstance(ctx.get("weather"), dict):
        weather = ctx.get("weather")
    wind = _num(weather.get("wind_kph") or weather.get("wind_speed_kph") or weather.get("wind_mps"))
    if weather.get("wind_mps") not in (None, ""):
        wind *= 3.6
    precip = _num(weather.get("precip_mm") or weather.get("rain_mm") or weather.get("snow_mm"))
    temp = _num(weather.get("temp_c") or weather.get("temperature_c"), 18.0)
    mod = 0.0
    reasons: list[str] = []
    if wind >= 28:
        mod += 2.0; reasons.append("strong_wind_under_bias")
    elif wind >= 18:
        mod += 1.0; reasons.append("wind_under_bias")
    if precip >= 3:
        mod += 1.0; reasons.append("precipitation_under_bias")
    if temp <= 0 or temp >= 32:
        mod += 0.7; reasons.append("extreme_temperature_risk")
    return {"totals_under_bias_pp": round(mod, 2), "reasons": reasons, "available": bool(weather)}


def reserve_quality_score(candidate: dict[str, Any], metrics: dict[str, Any]) -> dict[str, Any]:
    ev = max(_num(_get(candidate, metrics, "canonical_ev_pct", "ev_pct", "ev")), 0.0)
    edge = max(_num(_get(candidate, metrics, "canonical_edge_pp", "edge_pp", "edge")), 0.0)
    odds = _num(_get(candidate, metrics, "odds", "selected_odds", "price_used_for_ev"))
    books = max(_count(_get(candidate, metrics, "books_count", "bookmaker_count", "price_confirmation_count")), 0)
    odds_sources = max(_count(_get(candidate, metrics, "odds_sources_count", "line_sources_count", "sources_count")), 0)
    contexts = max(_count(_get(candidate, metrics, "context_sources_count", "confirmation_sources_count")), 0)
    confirmations = max(_count(_get(candidate, metrics, "confirmation_sources_count", "confirmations_count")), contexts)
    confidence = _num(_get(candidate, metrics, "confidence", "adjusted_confidence"), 70.0)
    league = league_reliability(candidate)
    weather = weather_modifier(candidate)

    score = 38.0
    score += min(18.0, ev * 1.45)
    score += min(16.0, edge * 3.0)
    score += min(10.0, books * 3.0)
    score += min(7.0, odds_sources * 3.5)
    score += min(10.0, confirmations * 1.5)
    score += max(-4.0, min(6.0, (confidence - 68.0) * 0.35))
    if 1.75 <= odds <= 2.55:
        score += 4.0
    elif 2.55 < odds <= 2.85:
        score += 1.5
    elif odds < 1.70 or odds > 2.90:
        score -= 8.0
    score += (league["score"] - 60.0) * 0.22
    # Weather is a small context modifier, not a pick generator.
    side = str(candidate.get("selection") or candidate.get("side") or "").lower()
    if weather["available"] and ("under" in side or "меньше" in side):
        score += min(2.0, weather["totals_under_bias_pp"])
    return {"score": round(max(0.0, min(100.0, score)), 1), "components": {"ev_pct": ev, "edge_pp": edge, "odds": odds, "books": books, "odds_sources": odds_sources, "confirmations": confirmations, "confidence": confidence, "league_reliability": league, "weather_modifier": weather}}


def _patch_candidate(candidate: dict[str, Any], metrics: dict[str, Any]) -> None:
    quality = reserve_quality_score(candidate, metrics)
    metrics["reserve_quality_score"] = quality["score"]
    metrics["reserve_quality_components"] = quality["components"]
    candidate["reserve_quality_score"] = quality["score"]
    candidate.setdefault("quality", quality["score"])
    candidate["reserve_quality_components"] = quality["components"]


def install(base: Any) -> dict[str, Any]:
    patched: dict[str, bool] = {}
    old_metrics = getattr(base, "candidate_metrics", None)
    if callable(old_metrics) and not getattr(base, "_harizon_quality_layer_metrics", False):
        def candidate_metrics(candidate: dict[str, Any]) -> dict[str, Any]:
            metrics = dict(old_metrics(candidate) or {})
            _patch_candidate(candidate, metrics)
            return metrics
        base.candidate_metrics = candidate_metrics
        base._harizon_quality_layer_metrics = True
        patched["candidate_metrics"] = True

    old_rank = getattr(base, "candidate_rank", None)
    if callable(old_rank) and not getattr(base, "_harizon_quality_layer_rank", False):
        def candidate_rank(item: Any):
            try:
                candidate = item[0] if isinstance(item, tuple) else item
                metrics = item[1] if isinstance(item, tuple) and len(item) > 1 and isinstance(item[1], dict) else {}
                rq = _num(metrics.get("reserve_quality_score") or candidate.get("reserve_quality_score")) if isinstance(candidate, dict) else 0.0
                original = old_rank(item)
                if isinstance(original, tuple):
                    return (rq, *original)
                return (rq, original)
            except Exception:
                return old_rank(item)
        base.candidate_rank = candidate_rank
        base._harizon_quality_layer_rank = True
        patched["candidate_rank"] = True

    old_publish = getattr(base, "publish_pick", None)
    if callable(old_publish) and not getattr(base, "_harizon_quality_layer_publish", False):
        def publish_pick(candidate: dict[str, Any], *args: Any, **kwargs: Any):
            result = old_publish(candidate, *args, **kwargs)
            try:
                LEDGER.parent.mkdir(parents=True, exist_ok=True)
                row = {"created_at_utc": datetime.now(UTC).isoformat(), "event": "published", "match_key": candidate.get("match_key") or candidate.get("canonical_match_id"), "home_team": candidate.get("home_team"), "away_team": candidate.get("away_team"), "league": candidate.get("league") or candidate.get("league_name"), "market": candidate.get("market") or candidate.get("family"), "selection": candidate.get("selection"), "point": candidate.get("point"), "odds": candidate.get("odds"), "tier": candidate.get("tier") or candidate.get("publication_tier"), "ev_pct": candidate.get("ev_pct"), "edge_pp": candidate.get("edge_pp"), "confidence": candidate.get("confidence"), "reserve_quality_score": candidate.get("reserve_quality_score"), "reserve_quality_components": candidate.get("reserve_quality_components"), "closing_odds": None, "result_score": None, "bet_result": None, "profit": None, "clv_pct": None}
                LEDGER.open("a", encoding="utf-8").write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
            except Exception:
                pass
            return result
        base.publish_pick = publish_pick
        base._harizon_quality_layer_publish = True
        patched["publish_pick"] = True

    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps({"status": "installed", "created_at_utc": datetime.now(UTC).isoformat(), "patched": patched, "ledger": str(LEDGER), "publication_contract_relaxed": False}, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"status": "installed", "patched": patched, "publication_contract_relaxed": False}


__all__ = ["install", "reserve_quality_score", "league_reliability", "weather_modifier"]
