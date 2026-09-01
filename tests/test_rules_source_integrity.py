from __future__ import annotations

from app.services import coverage_contract
from app.services.rules_source_integrity import install


def test_internal_and_derived_labels_are_not_context_sources():
    install()
    item = {
        "source_summary": {
            "context_sources": ["dayinventory", "market_implied_xg", "model_xg", "sstats_xg"]
        }
    }
    assert coverage_contract.context_sources_for_candidate(item) == {"sstats"}


def test_accounts_and_sstats_aliases_collapse_to_provider_family():
    install()
    item = {
        "raw_bucket_offers": [
            {"source": "odds_api_io_account1", "bookmaker": "Bet365"},
            {"source": "odds_api_io_account2", "bookmaker": "Unibet"},
            {"source": "sstats_current_odds", "bookmaker": "Pinnacle"},
        ]
    }
    assert coverage_contract.odds_sources_for_candidate(item) == {"odds_api_io", "sstats"}
