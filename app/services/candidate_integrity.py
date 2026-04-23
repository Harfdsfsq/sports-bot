from __future__ import annotations

from statistics import median
from typing import Any


def _to_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except Exception:
        return None


def _round(value: float | None, ndigits: int = 6) -> float | None:
    return None if value is None else round(float(value), ndigits)


def _first_float(*values: Any) -> float | None:
    for value in values:
        parsed = _to_float(value)
        if parsed is not None:
            return parsed
    return None


def _prob_from_odds(odds: float | None) -> float | None:
    if odds is None or odds <= 1.0:
        return None
    return 1.0 / odds


def _odds_from_prob(prob: float | None) -> float | None:
    if prob is None or prob <= 0.0 or prob >= 1.0:
        return None
    return 1.0 / prob


def _pick_canonical_probability(row: dict[str, Any]) -> tuple[float | None, list[str], dict[str, Any]]:
    ss = dict(row.get("source_summary") or {})
    candidates = {
        "adjusted_probability": _to_float(row.get("adjusted_probability")),
        "final_probability": _to_float(row.get("final_probability")),
        "source_summary.adjusted_probability": _to_float(ss.get("adjusted_probability")),
        "source_summary.final_probability": _to_float(ss.get("final_probability")),
    }
    usable = [value for value in candidates.values() if value is not None]
    reasons: list[str] = []
    if not usable:
        return None, ["missing_probability_values"], {"candidates": candidates}
    chosen = median(usable) if len(usable) >= 2 else usable[0]
    spread = max(usable) - min(usable) if len(usable) >= 2 else 0.0
    if spread > 0.02:
        reasons.append(f"probability_spread:{spread:.4f}")
    return chosen, reasons, {"candidates": {k: _round(v) for k, v in candidates.items()}, "spread": _round(spread)}


def canonicalize_candidate_dict(row: dict[str, Any]) -> dict[str, Any]:
    item = dict(row)
    ss = dict(item.get("source_summary") or {})
    diagnostics = dict(item.get("diagnostics") or {})
    reasons: list[str] = []

    selected_odds = _first_float(ss.get("selected_price"), item.get("odds"), ss.get("odds"))
    market_probability = _first_float(item.get("market_probability"), item.get("consensus_probability"), ss.get("market_probability"))
    implied_probability_old = _first_float(item.get("implied_probability"), ss.get("implied_probability"))
    canonical_prob, prob_reasons, prob_meta = _pick_canonical_probability(item)
    reasons.extend(prob_reasons)

    selected_implied = _prob_from_odds(selected_odds)
    fair_odds_from_market = _odds_from_prob(market_probability)

    price_used_for_ev = selected_odds
    probability_used_for_ev = canonical_prob
    edge_pct = None
    ev_pct = None

    if canonical_prob is not None and market_probability is not None:
        edge_pct = (canonical_prob - market_probability) * 100.0
    if canonical_prob is not None and selected_odds is not None:
        ev_pct = ((selected_odds * canonical_prob) - 1.0) * 100.0

    if selected_implied is not None and implied_probability_old is not None:
        mismatch = abs(selected_implied - implied_probability_old)
        if mismatch > 0.02:
            reasons.append(f"implied_mismatch:{mismatch:.4f}")

    ss_adjusted = _to_float(ss.get("adjusted_probability"))
    row_adjusted = _to_float(item.get("adjusted_probability"))
    if ss_adjusted is not None and row_adjusted is not None:
        mismatch = abs(ss_adjusted - row_adjusted)
        if mismatch > 0.02:
            reasons.append(f"adjusted_mismatch:{mismatch:.4f}")

    if edge_pct is not None and ev_pct is not None and edge_pct < 0.0 and ev_pct > 0.0:
        reasons.append("edge_ev_sign_conflict")

    status = "ok"
    if reasons:
        status = "reject" if any(
            text.startswith(("implied_mismatch", "adjusted_mismatch", "edge_ev_sign_conflict", "missing_probability_values"))
            for text in reasons
        ) else "warning"

    item["selected_odds"] = _round(selected_odds)
    item["selected_implied_probability"] = _round(selected_implied)
    item["fair_odds_from_market"] = _round(fair_odds_from_market)
    item["probability_used_for_ev"] = _round(probability_used_for_ev)
    item["price_used_for_ev"] = _round(price_used_for_ev)
    item["canonical_adjusted_probability"] = _round(canonical_prob)
    if price_used_for_ev is not None:
        item["odds"] = round(price_used_for_ev, 6)
    if selected_implied is not None:
        item["implied_probability"] = round(selected_implied, 6)
    if fair_odds_from_market is not None:
        item["fair_odds"] = round(fair_odds_from_market, 6)
    if market_probability is not None:
        item["market_probability"] = round(market_probability, 6)
        item["consensus_probability"] = round(market_probability, 6)
    if canonical_prob is not None:
        item["adjusted_probability"] = round(canonical_prob, 6)
        item["final_probability"] = round(canonical_prob, 6)

    if edge_pct is not None:
        item["edge_pct"] = round(edge_pct, 4)
    if ev_pct is not None:
        item["ev_pct"] = round(ev_pct, 4)

    raw_bucket_offers = ss.get("raw_bucket_offers") or item.get("raw_bucket_offers") or []
    item["raw_bucket_offers"] = raw_bucket_offers if isinstance(raw_bucket_offers, list) else []

    integrity = {
        "status": status,
        "reasons": reasons,
        "selected_odds": _round(selected_odds),
        "selected_implied_probability": _round(selected_implied),
        "market_probability": _round(market_probability),
        "fair_odds_from_market": _round(fair_odds_from_market),
        "canonical_adjusted_probability": _round(canonical_prob),
        "edge_pct": _round(edge_pct, 4),
        "ev_pct": _round(ev_pct, 4),
        "probability_meta": prob_meta,
    }
    item["integrity_status"] = status
    item["integrity_reasons"] = reasons
    item["integrity_report"] = integrity
    diagnostics["candidate_integrity"] = integrity
    item["diagnostics"] = diagnostics

    ss["selected_odds"] = item.get("selected_odds")
    ss["selected_implied_probability"] = item.get("selected_implied_probability")
    ss["fair_odds_from_market"] = item.get("fair_odds_from_market")
    ss["probability_used_for_ev"] = item.get("probability_used_for_ev")
    ss["price_used_for_ev"] = item.get("price_used_for_ev")
    ss["canonical_adjusted_probability"] = item.get("canonical_adjusted_probability")
    item["source_summary"] = ss
    return item
