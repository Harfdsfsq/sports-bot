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
class ContextObservation:
    match_key: str
    provider: str
    kind: str
    observed_at: datetime
    effective_at: datetime | None = None
    freshness_sec: int | None = None
    confidence: float | None = None
    metrics: dict[str, Any] = field(default_factory=dict)
    payload: dict[str, Any] = field(default_factory=dict)
    details: dict[str, Any] = field(default_factory=dict)
    provenance_hash: str = ""
    schema_version: int = 1


@dataclass(slots=True)
class MatchContextBundle:
    match_key: str
    contexts: list[ContextObservation] = field(default_factory=list)
    merged_context: MatchContext | None = None
    context_source_count: int = 0
    agreement_score: float | None = None
    provider_conflict_score: float | None = None
    has_weather: bool = False
    has_lineups: bool = False
    has_injuries: bool = False
    has_news: bool = False


@dataclass(slots=True)
class LineSnapshot:
    match_key: str
    market_key: str
    provider: str
    bookmaker: str
    family: MarketFamily
    selection: str
    price: float
    observed_at: datetime
    point: float | None = None
    team_side: str | None = None
    source_event_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    schema_version: int = 1


@dataclass(slots=True)
class ConsensusLine:
    match_key: str
    market_key: str
    family: MarketFamily
    selection: str
    point: float | None = None
    team_side: str | None = None
    best_price: float | None = None
    consensus_fair_odds: float | None = None
    books: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    snapshots_count: int = 0
    dispersion_pct: float | None = None
    steam_score: float | None = None


@dataclass(slots=True)
class MatchServing:
    match_key: str
    context_source_count: int = 0
    line_family_count: int = 0
    line_source_count: int = 0
    line_snapshot_count: int = 0
    line_snapshot_count_6h: int = 0
    agreement_score: float | None = None
    provider_conflict_score: float | None = None
    has_weather: bool = False
    has_lineups: bool = False
    has_injuries: bool = False
    has_news: bool = False
    steam_score: float | None = None
    best_market_movement: str | None = None
    context_sources: list[str] = field(default_factory=list)
    line_sources: list[str] = field(default_factory=list)
    line_families: list[str] = field(default_factory=list)


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
    selection_key: str
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
    bookmaker: str | None = None
    diagnostics: dict[str, Any] = field(default_factory=dict)
    analysis: dict[str, Any] = field(default_factory=dict)
    publication_score: float = 0.0
    source_event_id: str | None = None
    team_side: str | None = None
    stake_amount: float = 0.0
    stake_pct: float = 0.0
    bankroll_snapshot: float = 0.0
    bankroll_currency: str = "u"
    risk_label: str = "standard"
    already_used: bool = False

    selected_odds: float | None = None
    selected_implied_probability: float | None = None
    fair_odds_from_market: float | None = None
    probability_used_for_ev: float | None = None
    price_used_for_ev: float | None = None
    canonical_adjusted_probability: float | None = None
    integrity_status: str = "unknown"
    integrity_reasons: list[str] = field(default_factory=list)
    integrity_report: dict[str, Any] = field(default_factory=dict)
    raw_bucket_offers: list[dict[str, Any]] = field(default_factory=list)
