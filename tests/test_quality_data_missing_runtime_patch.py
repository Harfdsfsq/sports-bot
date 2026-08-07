from __future__ import annotations

from types import SimpleNamespace

from app.services.quality_data_missing_runtime_patch import mark_quality_data_missing, quality_data_missing


def _candidate(score=0.0, source="raw_missing"):
    return SimpleNamespace(
        match_key="soccer|alpha|beta|2026-08-07",
        source_summary={"quality_score": score, "quality_score_source": source},
        diagnostics={"quality": {"quality_score": score, "quality_score_source": source}},
        reasons=[],
    )


def test_zero_score_with_raw_missing_is_not_a_real_quality_score() -> None:
    candidate = _candidate()

    assert quality_data_missing(candidate) is True

    mark_quality_data_missing(candidate)

    assert candidate.source_summary["quality_status"] == "quality_data_missing"
    assert candidate.source_summary["quality_score"] is None
    assert candidate.diagnostics["quality"]["quality_score_source"] == "raw_missing"
    assert "quality=quality_data_missing" in candidate.reasons


def test_explicit_quality_source_is_not_relabelled_as_missing() -> None:
    candidate = _candidate(score=0.0, source="raw")
    candidate.source_summary["quality_sources"] = ["calibration"]

    assert quality_data_missing(candidate) is False
