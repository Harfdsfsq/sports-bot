from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class IntegrityCheckResult:
    ok: bool
    reasons: list[str]


class CandidateIntegrityService:
    def __init__(self, settings: Any) -> None:
        self.settings = settings

    def check(
        self,
        *,
        selected_odds: float,
        selected_implied_probability: float,
        implied_probability_field: float,
        canonical_adjusted_probability: float,
        source_adjusted_probability: float,
        fair_odds_from_market: float,
        edge_pct: float,
        ev_pct: float,
    ) -> IntegrityCheckResult:
        reasons: list[str] = []
        max_implied_delta = float(getattr(self.settings, 'phase12_odds_implied_max_delta', 0.02) or 0.02)
        max_adjusted_delta = float(getattr(self.settings, 'phase12_adjusted_max_delta', 0.02) or 0.02)
        max_ratio = float(getattr(self.settings, 'phase12_fair_odds_ratio_max', 1.20) or 1.20)
        if abs(float(selected_implied_probability) - float(implied_probability_field)) > max_implied_delta:
            reasons.append('odds_implied_mismatch')
        if abs(float(canonical_adjusted_probability) - float(source_adjusted_probability)) > max_adjusted_delta:
            reasons.append('adjusted_probability_mismatch')
        if float(edge_pct) < 0.0 and float(ev_pct) > 0.0:
            reasons.append('edge_ev_conflict')
        if float(fair_odds_from_market) > 0.0 and float(selected_odds) / float(fair_odds_from_market) > max_ratio:
            reasons.append('odds_to_fair_ratio_high')
        return IntegrityCheckResult(ok=len(reasons) == 0, reasons=reasons)
