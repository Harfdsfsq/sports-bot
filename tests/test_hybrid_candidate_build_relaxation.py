from __future__ import annotations

from app.services import core_line_bookmaker_universe_patch as patch


class DummySettings:
    min_sources_publish = 2
    market_derived_min_sources = 2
    market_derived_consensus_relief_min_sources = 2
    line_movement_min_sources = 2


def test_hybrid_candidate_build_relaxes_source_gates(monkeypatch):
    monkeypatch.setenv("CONTROLLED_FALLBACK_SINGLE_LINE_CONTEXT_MODE_ENABLED", "true")
    settings = DummySettings()

    old_values, report = patch._relax_candidate_build_contract(settings)

    assert report["applied"] is True
    assert settings.min_sources_publish == 1
    assert settings.market_derived_min_sources == 1
    assert settings.market_derived_consensus_relief_min_sources == 1

    patch._restore_candidate_build_contract(settings, old_values)

    assert settings.min_sources_publish == 2
    assert settings.market_derived_min_sources == 2
    assert settings.market_derived_consensus_relief_min_sources == 2


def test_hybrid_candidate_build_does_not_relax_when_disabled(monkeypatch):
    monkeypatch.delenv("CONTROLLED_FALLBACK_SINGLE_LINE_CONTEXT_MODE_ENABLED", raising=False)
    monkeypatch.delenv("CORE_LINE_BOOKMAKER_UNIVERSE_ALLOW_SINGLE_SOURCE", raising=False)
    settings = DummySettings()

    old_values, report = patch._relax_candidate_build_contract(settings)

    assert old_values == {}
    assert report["applied"] is False
    assert settings.min_sources_publish == 2
