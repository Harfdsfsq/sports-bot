from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any
import math


def to_float(value: Any, default: float | None = None) -> float | None:
    try:
        if value is None or value == "":
            return default
        number = float(value)
        if math.isnan(number) or math.isinf(number):
            return default
        return number
    except Exception:
        return default


def probability_from_odds(odds: float | None) -> float | None:
    if odds is None or odds <= 1.0:
        return None
    return 1.0 / odds


def pct_points(value: float | None) -> float | None:
    return None if value is None else value * 100.0


@dataclass(slots=True)
class CandidateIntegrityResult:
    ok: bool
    reasons: list[str]
    selected_odds: float | None
    selected_implied_probability: float | None
    stored_implied_probability: float | None
    market_probability: float | None
    fair_odds: float | None
    adjusted_probability: float | None
    source_summary_adjusted_probability: float | None
    edge_pct: float | None
    ev_pct: float | None
    fair_odds_ratio: float | None

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        for key, value in list(payload.items()):
            if isinstance(value, float):
                payload[key] = round(value, 6)
        return payload


class CandidateIntegrityService:
    """Canonical sanity checks for generated betting candidates.

    This class is intentionally side-effect free. It validates candidate
    payloads after model generation and before publication analysis.
    """

    def __init__(
        self,
        *,
        implied_tolerance_pp: float = 2.0,
        adjusted_tolerance_pp: float = 2.0,
        fair_odds_ratio_max: float = 1.20,
        reject_negative_edge_positive_ev: bool = True,
    ) -> None:
        self.implied_tolerance = max(0.0, float(implied_tolerance_pp)) / 100.0
        self.adjusted_tolerance = max(0.0, float(adjusted_tolerance_pp)) / 100.0
        self.fair_odds_ratio_max = max(1.0, float(fair_odds_ratio_max))
        self.reject_negative_edge_positive_ev = bool(reject_negative_edge_positive_ev)

    def validate(self, candidate: dict[str, Any]) -> CandidateIntegrityResult:
        source_summary = candidate.get("source_summary") if isinstance(candidate.get("source_summary"), dict) else {}

        selected_odds = (
            to_float(candidate.get("odds"))
            or to_float(candidate.get("selected_odds"))
            or to_float(source_summary.get("selected_price"))
            or to_float(source_summary.get("selected_odds"))
        )
        selected_implied = probability_from_odds(selected_odds)
        stored_implied = to_float(candidate.get("implied_probability"))

        market_probability = to_float(candidate.get("market_probability"))
        fair_odds = to_float(candidate.get("fair_odds"))
        if fair_odds is None and market_probability and market_probability > 0:
            fair_odds = 1.0 / market_probability

        adjusted = (
            to_float(candidate.get("adjusted_probability"))
            or to_float(candidate.get("final_probability"))
            or to_float(candidate.get("model_probability"))
        )
        source_adjusted = to_float(source_summary.get("adjusted_probability"))

        edge_pct = to_float(candidate.get("edge_pct"))
        ev_pct = to_float(candidate.get("ev_pct"))

        reasons: list[str] = []

        if selected_odds is None:
            reasons.append("missing_selected_odds")
        elif selected_odds <= 1.01:
            reasons.append("invalid_selected_odds")

        if selected_implied is not None and stored_implied is not None:
            gap = abs(selected_implied - stored_implied)
            if gap > self.implied_tolerance:
                reasons.append(f"implied_mismatch:{gap:.4f}")

        if adjusted is not None and source_adjusted is not None:
            gap = abs(adjusted - source_adjusted)
            if gap > self.adjusted_tolerance:
                reasons.append(f"adjusted_mismatch:{gap:.4f}")

        fair_ratio = None
        if selected_odds is not None and fair_odds is not None and fair_odds > 1.0:
            fair_ratio = selected_odds / fair_odds
            if fair_ratio > self.fair_odds_ratio_max:
                reasons.append(f"odds_far_above_fair:{fair_ratio:.3f}")

        if self.reject_negative_edge_positive_ev:
            if edge_pct is not None and ev_pct is not None and edge_pct < 0 and ev_pct > 0:
                reasons.append("negative_edge_positive_ev")

        return CandidateIntegrityResult(
            ok=not reasons,
            reasons=reasons,
            selected_odds=selected_odds,
            selected_implied_probability=selected_implied,
            stored_implied_probability=stored_implied,
            market_probability=market_probability,
            fair_odds=fair_odds,
            adjusted_probability=adjusted,
            source_summary_adjusted_probability=source_adjusted,
            edge_pct=edge_pct,
            ev_pct=ev_pct,
            fair_odds_ratio=fair_ratio,
        )

    def canonicalize(self, candidate: dict[str, Any]) -> dict[str, Any]:
        result = self.validate(candidate)
        payload = dict(candidate)
        payload["selected_odds"] = result.selected_odds
        payload["selected_implied_probability"] = result.selected_implied_probability
        payload["canonical_adjusted_probability"] = result.adjusted_probability
        payload["canonical_integrity"] = result.as_dict()
        diagnostics = dict(payload.get("diagnostics") or {})
        diagnostics["candidate_integrity"] = result.as_dict()
        payload["diagnostics"] = diagnostics
        return payload
