from __future__ import annotations

from scripts import build_b_cover_candidate_gap_report as bcover


def _bucket() -> dict:
    return {
        "books": {"betfair_exchange", "sbobet"},
        "rows": [
            {"bookmaker": "betfair_exchange", "price": 2.00},
            {"bookmaker": "sbobet", "price": 2.00},
            {"bookmaker": "betfair_exchange", "price": 2.10},
        ],
    }


def _inventory_row() -> dict:
    return {
        "match_key": "soccer|qadsia|al salmiya|2026-06-16",
        "home_team": "Qadsia",
        "away_team": "Al-Salmiya",
        "league_name": "Kuwait - Premier League",
        "commence_time": "2026-06-16T18:45:00+00:00",
        "context_sources": ["model_xg", "sstats"],
        "expected_home": None,
        "expected_away": None,
    }


def test_b_cover_promotion_drops_model_xg_when_values_are_missing(monkeypatch) -> None:
    monkeypatch.setenv("PROMOTE_B_COVER_MIN_EDGE_PP", "0")
    monkeypatch.setenv("PROMOTE_B_COVER_MIN_EV_PCT", "0")

    candidate, status = bcover.build_candidate_from_bucket(_inventory_row(), "totals|under|2.5", _bucket())

    assert status == "promoted"
    assert candidate is not None
    assert "model_xg" not in candidate["confirmation_sources"]
    assert "model_xg" not in candidate["source_summary"]["context_sources"]
    assert candidate["expected_home"] is None
    assert candidate["expected_away"] is None


def test_b_cover_promotion_keeps_model_xg_when_values_exist(monkeypatch) -> None:
    monkeypatch.setenv("PROMOTE_B_COVER_MIN_EDGE_PP", "0")
    monkeypatch.setenv("PROMOTE_B_COVER_MIN_EV_PCT", "0")
    row = _inventory_row()
    row["expected_home"] = 1.2
    row["expected_away"] = 1.1

    candidate, status = bcover.build_candidate_from_bucket(row, "totals|under|2.5", _bucket())

    assert status == "promoted"
    assert candidate is not None
    assert "model_xg" in candidate["confirmation_sources"]
    assert candidate["expected_home"] == 1.2
    assert candidate["expected_away"] == 1.1
