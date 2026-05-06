from __future__ import annotations

from types import SimpleNamespace

from app.services.market_integrity import validate_candidate


def candidate(**overrides):
    base = {
        "family": "totals",
        "selection": "Over",
        "selection_key": "over_1_5",
        "point": 1.5,
        "odds": 1.98,
        "model_mode": "controlled_fallback",
        "sources_count": 1,
        "books_count": 1,
        "source_summary": {},
        "diagnostics": {},
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_rejects_over_15_with_high_price_without_exact_market_depth(monkeypatch):
    monkeypatch.setenv("MARKET_INTEGRITY_USE_EXACT_PRICE_SOURCES", "true")
    item = candidate(
        raw_bucket_offers=[
            {"source": "odds_api_io", "bookmaker": "Bet365", "family": "totals", "selection": "Over", "point": 1.5, "price": 1.98},
            {"source": "newsapi", "bookmaker": "", "family": "news", "selection": "context", "price": 1.0},
        ]
    )

    decision = validate_candidate(item)

    assert not decision.passed
    assert any(reason.startswith("suspicious_low_total_exact_depth") for reason in decision.reasons)
    assert decision.report["exact_books_count"] == 1
    assert decision.report["exact_sources_count"] == 1


def test_context_sources_do_not_count_as_price_confirmation(monkeypatch):
    monkeypatch.setenv("PROVIDER_CONTEXT_SOURCES_DO_NOT_CONFIRM_PRICE", "true")
    item = candidate(
        odds=1.72,
        raw_bucket_offers=[
            {"source": "newsapi", "bookmaker": "News", "family": "totals", "selection": "Over", "point": 1.5, "price": 1.72},
            {"source": "weatherapi", "bookmaker": "Weather", "family": "totals", "selection": "Over", "point": 1.5, "price": 1.70},
        ],
    )

    decision = validate_candidate(item)

    assert not decision.passed
    assert decision.report["sources_count"] == 0
    assert any(reason.startswith("insufficient_sources") for reason in decision.reasons)


def test_passes_normal_over_15_when_price_is_reasonable_and_depth_exists(monkeypatch):
    monkeypatch.setenv("MARKET_INTEGRITY_USE_EXACT_PRICE_SOURCES", "true")
    item = candidate(
        odds=1.42,
        sources_count=2,
        books_count=3,
        raw_bucket_offers=[
            {"source": "odds_api_io", "bookmaker": "Bet365", "family": "totals", "selection": "Over", "point": 1.5, "price": 1.42},
            {"source": "odds_api_io", "bookmaker": "Unibet", "family": "totals", "selection": "Over", "point": 1.5, "price": 1.43},
            {"source": "sportlogic", "bookmaker": "Sbobet", "family": "totals", "selection": "Over", "point": 1.5, "price": 1.41},
        ],
    )

    decision = validate_candidate(item)

    assert decision.passed, decision.reasons
    assert decision.report["exact_books_count"] == 3
