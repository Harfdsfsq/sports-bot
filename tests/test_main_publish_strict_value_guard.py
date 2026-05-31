from __future__ import annotations

from dataclasses import dataclass, field

from app.services.main_publish_strict_value_guard import strict_reject_reasons


@dataclass
class DummyCandidate:
    league_name: str = "Brazil - Brasileiro Serie D"
    model_mode: str = "controlled_consensus_rescue"
    ev_pct: float = 2.32
    edge_pct: float = 2.49
    confidence: float = 78.0
    quality_score: float = 66.1
    source_summary: dict = field(default_factory=dict)
    diagnostics: dict = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)


def test_non_core_rescue_candidate_is_blocked_when_ev_quality_low(monkeypatch):
    monkeypatch.delenv("MAIN_PUBLISH_NON_CORE_RESCUE_MIN_EV_PCT", raising=False)
    candidate = DummyCandidate()
    reasons = strict_reject_reasons(candidate)
    assert any("main_publish_non_core_rescue_ev_below_min" in r for r in reasons)
    assert any("main_publish_non_core_rescue_quality_below_min" in r for r in reasons)


def test_strong_non_core_rescue_candidate_can_pass(monkeypatch):
    candidate = DummyCandidate(ev_pct=5.1, edge_pct=4.0, confidence=80.0, quality_score=74.0)
    assert strict_reject_reasons(candidate) == []


def test_non_rescue_candidate_is_not_touched():
    candidate = DummyCandidate(model_mode="xg_model", ev_pct=1.0, edge_pct=1.0, quality_score=40.0)
    assert strict_reject_reasons(candidate) == []
