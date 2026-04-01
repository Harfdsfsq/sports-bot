from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

SportKey = Literal['soccer', 'basketball', 'baseball', 'icehockey']
MarketFamily = Literal['h2h', 'totals', 'spreads']


@dataclass(slots=True)
class Match:
    source: str
    source_event_id: str
    sport_key: SportKey
    league_name: str
    home_team: str
    away_team: str
    commence_time: datetime
    home_team_norm: str
    away_team_norm: str
    tier: str = 'mid'
    metadata: dict = field(default_factory=dict)

    @property
    def match_key(self) -> str:
        return (
            f"{self.sport_key}|{self.home_team_norm}|{self.away_team_norm}|"
            f"{self.commence_time.date().isoformat()}"
        )


@dataclass(slots=True)
class Offer:
    source: str
    bookmaker: str
    family: MarketFamily
    selection: str
    price: float
    point: float | None = None
    source_event_id: str | None = None


@dataclass(slots=True)
class MatchContext:
    source: str
    payload: dict
    expected_home: float | None = None
    expected_away: float | None = None
    injuries_score_home: float = 0.0
    injuries_score_away: float = 0.0
    market_pressure: float = 0.0


@dataclass(slots=True)
class CandidateBet:
    match_key: str
    sport_key: SportKey
    league_name: str
    home_team: str
    away_team: str
    commence_time: datetime
    family: MarketFamily
    selection: str
    odds: float
    fair_odds: float
    implied_probability: float
    model_probability: float
    adjusted_probability: float
    edge_pct: float
    ev_pct: float
    confidence: float
    books_count: int
    sources_count: int
    expected_home: float | None = None
    expected_away: float | None = None
    point: float | None = None
    reasons: list[str] = field(default_factory=list)
    source_summary: dict = field(default_factory=dict)
