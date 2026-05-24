from __future__ import annotations

from types import SimpleNamespace


def test_api_coverage_soft_guard_keeps_discovery_candidate(monkeypatch):
    import app.services.api_coverage_consensus_runtime_patch as api_cov
    import app.services.api_coverage_discovery_soft_guard_patch as patch

    monkeypatch.setenv("API_COVERAGE_DISCOVERY_SOFT_GUARD_ENABLED", "true")

    def fake_guard(candidate, contexts_by_match):
        return False, "api_coverage_missing_2_exact_odds_sources", {
            "exact_odds_sources_count": 1,
            "exact_odds_sources": ["odds_api_io"],
            "exact_books_count": 2,
        }

    monkeypatch.setattr(api_cov, "_guard_candidate", fake_guard, raising=False)
    monkeypatch.setattr(patch, "_INSTALLED", False, raising=False)
    result = patch.install()
    assert result["status"] in {"installed", "already_wrapped"}

    candidate = SimpleNamespace(source_summary={}, diagnostics={}, reasons=[])
    ok, reason, inv = api_cov._guard_candidate(candidate, {})

    assert ok is True
    assert reason.startswith("soft_discovery:")
    assert candidate.source_summary["publish_coverage_passed"] is False
    assert candidate.source_summary["publication_blocked_reason"] == "api_coverage_missing_2_exact_odds_sources"
    assert "api_coverage_discovery_soft:api_coverage_missing_2_exact_odds_sources" in candidate.reasons


def test_api_coverage_soft_guard_does_not_soften_value_reject(monkeypatch):
    import app.services.api_coverage_consensus_runtime_patch as api_cov
    import app.services.api_coverage_discovery_soft_guard_patch as patch

    def fake_guard(candidate, contexts_by_match):
        return False, "api_coverage_consensus_value_not_positive", {}

    monkeypatch.setattr(api_cov, "_guard_candidate", fake_guard, raising=False)
    monkeypatch.setattr(patch, "_INSTALLED", False, raising=False)
    patch.install()

    candidate = SimpleNamespace(source_summary={}, diagnostics={}, reasons=[])
    ok, reason, inv = api_cov._guard_candidate(candidate, {})

    assert ok is False
    assert reason == "api_coverage_consensus_value_not_positive"
    assert candidate.reasons == []
