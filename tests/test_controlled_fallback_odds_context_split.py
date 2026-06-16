from __future__ import annotations

from scripts import publish_controlled_fallback as pcf


def _candidate() -> dict:
    return {
        "match_key": "soccer|acf fiorentina|atalanta|2026-05-22",
        "family": "totals",
        "selection": "Меньше",
        "point": 2.5,
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


def test_totals_xg_sanity_reads_nested_sstats_payload(monkeypatch):
    monkeypatch.setenv("CONTROLLED_FALLBACK_REQUIRE_MATCH_TIME", "false")
    candidate = _candidate()
    candidate.pop("expected_home", None)
    candidate.pop("expected_away", None)
    candidate["provider_context"] = {
        "source": "sstats",
        "payload": {
            "ExpectedGoalsHome": 1.42,
            "ExpectedGoalsAway": 1.08,
        },
    }

    metrics = pcf.candidate_metrics(candidate)

    assert metrics["xg_sanity"]["enabled"] is True
    assert metrics["xg_sanity"]["xg_total"] == 2.5
    assert "missing_total_xg_sanity" not in pcf.hard_reject_reasons(candidate, metrics, {})


def test_totals_xg_sanity_uses_nested_total_xg(monkeypatch):
    monkeypatch.setenv("CONTROLLED_FALLBACK_REQUIRE_MATCH_TIME", "false")
    candidate = _candidate()
    candidate.pop("expected_home", None)
    candidate.pop("expected_away", None)
    candidate["context_observations"] = [
        {"source": "sstats", "details": {"total_xg": 2.5}},
    ]

    metrics = pcf.candidate_metrics(candidate)

    assert metrics["xg_sanity"]["enabled"] is True
    assert metrics["xg_sanity"]["xg_source"] == "total_xg"
    assert metrics["xg_sanity"]["xg_total"] == 2.5
    assert "missing_total_xg_sanity" not in pcf.hard_reject_reasons(candidate, metrics, {})


def test_quarter_total_line_is_never_publishable(monkeypatch):
    monkeypatch.setenv("CONTROLLED_FALLBACK_REQUIRE_MATCH_TIME", "false")
    candidate = _candidate()
    candidate["point"] = 2.25

    metrics = pcf.candidate_metrics(candidate)
    reasons = pcf.hard_reject_reasons(candidate, metrics, {})

    assert "market_point:quarter_totals_not_allowed" in reasons


def test_b_tier_bookmaker_quorum_accepts_one_api_two_books(monkeypatch):
    monkeypatch.setenv("CONTROLLED_FALLBACK_REQUIRE_MATCH_TIME", "false")
    monkeypatch.setenv("CONTROLLED_FALLBACK_REQUIRE_LINE_MOVEMENT_FOR_TELEGRAM", "false")
    candidate = _candidate()
    candidate["books_count"] = 2
    candidate["raw_bucket_offers"] = [
        {"source": "odds_api_io", "bookmaker": "Bet365", "family": "totals", "selection": "Under", "point": 2.5, "price": 2.18},
        {"source": "odds_api_io", "bookmaker": "Unibet", "family": "totals", "selection": "Under", "point": 2.5, "price": 2.22},
    ]
    candidate["odds"] = 2.20

    metrics = pcf.candidate_metrics(candidate)
    reasons = pcf.tier_reasons("B", candidate, metrics)

    assert metrics["odds_sources_count"] == 1
    assert metrics["books_count"] == 2
    assert not [reason for reason in reasons if "bookmaker_quorum" in reason]


def test_b_tier_bookmaker_quorum_rejects_price_outlier(monkeypatch):
    monkeypatch.setenv("CONTROLLED_FALLBACK_REQUIRE_MATCH_TIME", "false")
    monkeypatch.setenv("CONTROLLED_FALLBACK_TIER_B_MAX_BOOKMAKER_MEDIAN_DEVIATION_PCT", "5")
    candidate = _candidate()
    candidate["books_count"] = 2
    candidate["raw_bucket_offers"] = [
        {"source": "odds_api_io", "bookmaker": "Bet365", "family": "totals", "selection": "Under", "point": 2.5, "price": 1.80},
        {"source": "odds_api_io", "bookmaker": "Unibet", "family": "totals", "selection": "Under", "point": 2.5, "price": 1.82},
    ]
    candidate["odds"] = 2.20

    metrics = pcf.candidate_metrics(candidate)
    reasons = pcf.tier_reasons("B", candidate, metrics)

    assert any(str(reason).startswith("tier_b_bookmaker_quorum_price_outlier") for reason in reasons)


def test_b_tier_bookmaker_quorum_uses_same_side_same_line_only(monkeypatch):
    monkeypatch.setenv("CONTROLLED_FALLBACK_REQUIRE_MATCH_TIME", "false")
    monkeypatch.setenv("CONTROLLED_FALLBACK_TIER_B_MAX_BOOKMAKER_MEDIAN_DEVIATION_PCT", "8")
    candidate = _candidate()
    candidate["books_count"] = 2
    candidate["raw_bucket_offers"] = [
        {"source": "odds_api_io", "bookmaker": "Bet365", "family": "totals", "selection": "Over", "point": 2.5, "price": 2.20},
        {"source": "odds_api_io", "bookmaker": "Unibet", "family": "totals", "selection": "Over", "point": 2.5, "price": 2.18},
        {"source": "odds_api_io", "bookmaker": "Bet365", "family": "totals", "selection": "Under", "point": 2.5, "price": 1.42},
        {"source": "odds_api_io", "bookmaker": "Unibet", "family": "totals", "selection": "Under", "point": 2.5, "price": 1.44},
    ]
    candidate["odds"] = 2.20

    metrics = pcf.candidate_metrics(candidate)
    reasons = pcf.tier_reasons("B", candidate, metrics)

    assert any(str(reason).startswith("tier_b_bookmaker_quorum_price_outlier") for reason in reasons)
    assert metrics["tier_b_bookmaker_quorum"]["same_market_side_line_only"] is True


def test_telegram_text_blocks_quarter_total_line():
    from scripts import telegram_controlled_pick_safety as safety

    text = "controlled fallback\nСтавка: Тотал Больше 2.25\nКоэффициент: 1.95\nodds sources: 1"

    reasons = safety._text_reasons(text)

    assert "telegram_quarter_total_line_not_allowed:2.25" in reasons


def test_telegram_text_blocks_c_signal_profile():
    from scripts import telegram_controlled_pick_safety as safety

    text = (
        "1. Vikingur Reykjavik - KR Reykjavik\n"
        "Signal profile: C 60.7/100 | quality 66.8 | lines 3 | sources 1 | risk: single-source\n"
        "Stake: totals under 4.5 @ 1.80"
    )

    reasons = safety._text_reasons(text)

    assert "telegram_signal_profile_c_blocked" in reasons


def test_telegram_text_blocks_single_source_non_core_stack():
    from scripts import telegram_controlled_pick_safety as safety

    text = (
        "best bet\n"
        "Signal profile: B 66.7/100 | quality 70.2 | lines 3 | sources 1 | risk: single-source, non-core\n"
        "Stake: totals under 4.5 @ 1.80"
    )

    reasons = safety._text_reasons(text)

    assert "telegram_single_source_non_core_blocked" in reasons
