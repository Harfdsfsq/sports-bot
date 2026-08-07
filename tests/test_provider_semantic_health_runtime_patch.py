from __future__ import annotations

from app.services import provider_semantic_health_runtime_patch as health


def test_sstats_rows_without_direct_or_form_matches_are_semantic_failure() -> None:
    stats = {
        "rows_fetched": 10749,
        "matched_exact": 0,
        "matched_loose": 0,
        "matched_fuzzy": 0,
        "team_form_contexts_built": 0,
        "contexts_built": 0,
        "response_errors": 0,
    }
    metrics = health.stage_metrics("sstats", stats, {})

    assert metrics["rows_received"] == 10749
    assert metrics["rows_matched"] == 0
    assert health.classify_status("sstats", stats, metrics, {}) == "degraded_semantic_no_match"


def test_bzzoiro_transport_failure_is_degraded() -> None:
    stats = {
        "events_fetched": 0,
        "event_matches": 0,
        "response_errors": 4,
    }
    metrics = health.stage_metrics("bzzoiro", stats, {})

    assert health.classify_status("bzzoiro", stats, metrics, {}) == "degraded_transport_error"


def test_provider_with_rows_and_usable_context_is_healthy() -> None:
    stats = {
        "rows_fetched": 10,
        "matched_exact": 2,
        "matched_loose": 0,
        "matched_fuzzy": 0,
        "contexts_built": 2,
        "response_errors": 0,
    }
    metrics = health.stage_metrics("sstats", stats, {"m1": object(), "m2": object()})

    assert health.classify_status("sstats", stats, metrics, {"m1": object(), "m2": object()}) == "healthy"
