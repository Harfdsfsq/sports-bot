from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class PredictionRun(Base):
    __tablename__ = 'prediction_runs'

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default='running', nullable=False)
    summary: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error_text: Mapped[str | None] = mapped_column(Text, nullable=True)


class PublishedBet(Base):
    __tablename__ = 'published_bets'
    __table_args__ = (UniqueConstraint('match_key', 'family', 'selection', 'commence_time', name='uq_pubbet_unique'),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(Integer, nullable=False)
    match_key: Mapped[str] = mapped_column(String(255), nullable=False)
    sport_key: Mapped[str] = mapped_column(String(32), nullable=False)
    league_name: Mapped[str] = mapped_column(String(255), nullable=False)
    home_team: Mapped[str] = mapped_column(String(255), nullable=False)
    away_team: Mapped[str] = mapped_column(String(255), nullable=False)
    commence_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    family: Mapped[str] = mapped_column(String(32), nullable=False)
    selection: Mapped[str] = mapped_column(String(64), nullable=False)
    point: Mapped[float | None] = mapped_column(Float, nullable=True)
    odds_published: Mapped[float] = mapped_column(Float, nullable=False)
    fair_odds: Mapped[float] = mapped_column(Float, nullable=False)
    implied_probability: Mapped[float] = mapped_column(Float, nullable=False)
    model_probability: Mapped[float] = mapped_column(Float, nullable=False)
    adjusted_probability: Mapped[float] = mapped_column(Float, nullable=False)
    edge_pct: Mapped[float] = mapped_column(Float, nullable=False)
    ev_pct: Mapped[float] = mapped_column(Float, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    books_count: Mapped[int] = mapped_column(Integer, nullable=False)
    sources_count: Mapped[int] = mapped_column(Integer, nullable=False)
    reasons: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    source_summary: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    telegram_sent: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    close_odds: Mapped[float | None] = mapped_column(Float, nullable=True)
    settlement: Mapped[str | None] = mapped_column(String(16), nullable=True)
    roi_units: Mapped[float | None] = mapped_column(Float, nullable=True)
