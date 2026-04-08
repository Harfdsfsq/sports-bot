from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

SportKey = Literal["soccer", "basketball", "baseball", "icehockey"]
MarketFamily = Literal["h2h", "totals", "spreads", "dnb", "doubleChance", "btts", "teamTotals"]


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
    league_key: str
    tier: str = "mid"
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def match_key(self) -> str:
        from app.utils import build_match_key
        return build_match_key(self.sport_key, self.home_team, self.away_team, self.commence_time)

    @property
    def loose_key(self) -> str:
        from app.utils import build_loose_match_key
        return build_loose_match_key(self.sport_key, self.home_team, self.away_team)


@dataclass(slots=True)
class Offer:
    source: str
    bookmaker: str
    family: MarketFamily
    selection: str
    price: float
    point: float | None = None
    team_side: str | None = None
    market_name: str = ""
    market_key: str = ""
    market_subtype: str = ""
    source_event_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class MatchContext:
    source: str
    payload: dict[str, Any]
    expected_home: float | None = None
    expected_away: float | None = None
    home_win_probability: float | None = None
    away_win_probability: float | None = None
    home_starting: int | None = None
    away_starting: int | None = None
    confidence: float = 58.0
    profits: dict[str, Any] = field(default_factory=dict)
    details: dict[str, Any] = field(default_factory=dict)


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
    market_probability: float
    consensus_probability: float
    model_probability: float
    final_probability: float
    adjusted_probability: float
    edge_pct: float
    ev_pct: float
    confidence: float
    books_count: int
    sources_count: int
    model_mode: str = "market_only"
    point: float | None = None
    expected_home: float | None = None
    expected_away: float | None = None
    reasons: list[str] = field(default_factory=list)
    source_summary: dict[str, Any] = field(default_factory=dict)
    diagnostics: dict[str, Any] = field(default_factory=dict)
    publication_score: float = 0.0
    analysis: dict[str, Any] = field(default_factory=dict)
    already_used: bool = False
