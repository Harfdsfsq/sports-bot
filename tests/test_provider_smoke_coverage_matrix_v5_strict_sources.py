from __future__ import annotations

from scripts import provider_smoke_coverage_matrix as base
from scripts.provider_smoke_coverage_matrix_v5 import _patched_source_count


def test_single_sstats_context_is_not_doubled_by_context_xg_form_flags() -> None:
    row = {
        "context_sources": ["sstats"],
        "coverage": {"context": True, "xg": True, "form": True},
        "context_sources_count": 2,
        "confirmation_sources_count": 4,
        "xg_sources_count": 2,
        "form_sources_count": 2,
    }

    assert _patched_source_count(row, base.CONTEXT_COUNT_KEYS) == 1


def test_two_named_context_providers_count_as_two() -> None:
    row = {
        "context_sources": ["sstats", "bzzoiro"],
        "coverage": {"context": True, "xg": True, "form": True},
    }

    assert _patched_source_count(row, base.CONTEXT_COUNT_KEYS) == 2


def test_verified_context_sources_override_stale_top_level_sources() -> None:
    row = {
        "context_sources": ["sstats", "bzzoiro"],
        "metadata": {"verified_context_sources": ["bzzoiro"]},
        "coverage": {"context": True, "daily_coverage_evidence_synced": True},
    }

    assert _patched_source_count(row, base.CONTEXT_COUNT_KEYS) == 1


def test_explicit_empty_verified_context_is_authoritative() -> None:
    row = {
        "context_sources": ["sstats"],
        "metadata": {"verified_context_sources": []},
        "coverage": {"context": True, "form": True, "daily_coverage_evidence_synced": True},
    }

    assert _patched_source_count(row, base.CONTEXT_COUNT_KEYS) == 0


def test_bookmaker_count_does_not_become_independent_odds_sources() -> None:
    row = {
        "books_count": 8,
        "price_confirmation_sources_count": 8,
        "odds_sources": ["odds_api_io"],
        "coverage": {"odds": True},
    }

    assert _patched_source_count(row, base.ODDS_COUNT_KEYS) == 1


def test_verified_odds_sources_override_stale_line_aliases() -> None:
    row = {
        "odds_sources": ["odds_api_io", "bzzoiro", "sstats_pari"],
        "line_sources": ["odds_api_io", "bzzoiro"],
        "metadata": {"verified_odds_sources": ["odds_api_io", "bzzoiro"]},
        "coverage": {"odds": True, "daily_coverage_evidence_synced": True},
    }

    assert _patched_source_count(row, base.ODDS_COUNT_KEYS) == 2
