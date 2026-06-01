from __future__ import annotations

from scripts import publish_controlled_fallback as pcf


def _candidate() -> dict:
    return {
        "match_key": "soccer|acf fiorentina|atalanta|2026-05-22",
        "family": "totals",
        "selection": "Меньше",
        "point": 2.25,
        "odds": 2.705,
        "adjusted_probability": 0.436691,
        "model_probability": 0.722557,
        "market_probability": 0.352535,
        "confidence": 67.654,
        "publication_score": 79.03,
        "quality_score": 96.449,
        "books_count": 2,
        "sources_count": 2,
        "expected_home": 0.8889,
        "expected_away": 0.9412,
        "source_summary": {
            "selected_bookmaker": "Betfair Exchange",
            "selected_source": "odds_api_io",
            "context_sources": ["bzzoiro", "context_equiv_supplemental", "odds_api_io", "sstats", "weather"],
            "line_sources": ["bzzoiro", "odds_api_io"],
            "publish_coverage_passed": False,
            "publish_coverage_reasons": ["insufficient_odds_sources:1/2"],
            "publish_coverage_contract": {
                "books_count": 2,
                "context_sources": ["bzzoiro", "context_equiv_supplemental", "odds_api_io", "sstats", "weather"],
                "context_sources_count": 5,
                "odds_sources": ["odds_api_io"],
                "odds_sources_count": 1,
            },
            "exact_odds_sources": ["odds_api_io_account1", "odds_api_io_account2"],
            "exact_odds_sources_count": 2,
            "exact_books": ["betfair_exchange", "unibet"],
            "exact_books_count": 2,
        },
        "diagnostics": {
            "quality": {
                "quality_score": 96.449,
                "final_adjusted_probability": 0.436691,
                "reasons": ["bad_historical_segment_guard", "quality_historical_guard_relief"],
            }
        },
    }


def test_sstats_context_does_not_inflate_odds_sources(monkeypatch):
    monkeypatch.setenv("CONTROLLED_FALLBACK_REQUIRE_MATCH_TIME", "false")
    candidate = _candidate()
    metrics = pcf.candidate_metrics(candidate)

    assert metrics["odds_sources_count"] == 1
    assert metrics["line_sources"] == ["odds_api_io"]
    assert "sstats" in metrics["confirmation_sources"]
    assert "odds_api_io" not in metrics["confirmation_sources"]


def test_single_provider_candidate_not_promoted_to_tier_a(monkeypatch):
    monkeypatch.setenv("CONTROLLED_FALLBACK_REQUIRE_MATCH_TIME", "false")
    monkeypatch.setenv("CONTROLLED_FALLBACK_TIER_B_MIN_CONFIDENCE", "76")
    candidate = _candidate()
    ok, reasons, metrics, tier = pcf.evaluate_candidate(candidate, {})

    assert not ok
    assert tier != "уровень A"
    assert "tier_a_odds_sources_below_min:1/2" in pcf.tier_reasons("A", candidate, metrics)
    assert any(str(reason).startswith("telegram_publish_odds_sources_guard") or str(reason) == "tier_c_watch_only" for reason in reasons)


def test_over_total_below_xg_line_is_direction_conflict(monkeypatch):
    monkeypatch.setenv("CONTROLLED_FALLBACK_REQUIRE_MATCH_TIME", "false")
    candidate = _candidate()
    candidate.update(
        {
            "selection": "Over 2.5",
            "point": 2.5,
            "expected_home": 1.39,
            "expected_away": 1.00,
            "adjusted_probability": 0.442,
            "odds": 2.48,
            "source_summary": {
                **candidate["source_summary"],
                "publish_coverage_contract": {
                    "books_count": 5,
                    "context_sources": ["bzzoiro", "highlightly", "sstats"],
                    "context_sources_count": 3,
                    "odds_sources": ["bzzoiro", "odds_api_io"],
                    "odds_sources_count": 2,
                },
                "line_sources": ["bzzoiro", "odds_api_io"],
                "context_sources": ["bzzoiro", "highlightly", "sstats"],
            },
        }
    )

    metrics = pcf.candidate_metrics(candidate)

    assert metrics["xg_sanity"]["xg_total"] == 2.39
    assert metrics["xg_sanity"]["xg_direction_ok"] is False
    assert "xg_direction_conflict" in pcf.hard_reject_reasons(candidate, metrics, {})
