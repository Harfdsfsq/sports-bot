from scripts.publish_controlled_fallback_with_run_context import _independent_odds_sources


def test_odds_api_accounts_and_books_count_as_one_provider():
    candidate = {
        "raw_bucket_offers": [
            {"source": "odds_api_io", "bookmaker": "Bet365", "price": 1.90},
            {"source": "odds_api_io", "bookmaker": "Betfair Exchange", "price": 1.92},
        ],
        "diagnostics": {
            "api_coverage_consensus": {"exact_odds_sources": ["odds_api_io_account1", "odds_api_io_account2"]},
            "publish_coverage_contract": {"odds_sources": ["odds_api_io"], "odds_sources_count": 1},
        },
        "source_summary": {"odds_sources": ["odds_api_io"], "price_sources_count": 2},
    }
    sources, count, _ = _independent_odds_sources(candidate)
    assert sources == ["odds_api_io"]
    assert count == 1


def test_bzzoiro_plus_odds_api_counts_as_two_providers():
    candidate = {
        "raw_bucket_offers": [
            {"source": "odds_api_io", "bookmaker": "Bet365"},
            {"source": "bzzoiro", "bookmaker": "Bzzoiro"},
        ],
    }
    sources, count, _ = _independent_odds_sources(candidate)
    assert sources == ["bzzoiro", "odds_api_io"]
    assert count == 2
