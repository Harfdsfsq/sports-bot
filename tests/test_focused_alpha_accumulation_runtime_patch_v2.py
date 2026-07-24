from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.services import focused_alpha_accumulation_runtime_patch_v2 as patch


def test_strict_evidence_excludes_books_and_pseudo_sources() -> None:
    row = {
        "raw_decision_snapshot": {
            "odds_sources": [
                "odds_api_io",
                "book_unibet",
                "bet365",
                "market",
            ],
            "context_sources": [
                "sstats",
                "market",
                "day_inventory",
                "odds_api_io",
            ],
            "bookmakers": ["unibet", "bet365"],
            "expected_home": 1.2,
            "expected_away": 1.1,
            "xg_source": "market_implied_total_xg",
        }
    }

    evidence = patch._strict_evidence(row)

    assert evidence["odds_sources"] == ["odds_api_io"]
    assert evidence["context_sources"] == ["sstats"]
    assert evidence["bookmakers"] == ["bet365", "unibet"]
    assert "market_implied_total_xg" in evidence["xg_sources"]


def test_canonical_decision_identity_survives_team_and_selection_variants() -> None:
    kickoff = datetime.now(UTC) + timedelta(hours=3)
    first = {
        "home_team": "Pogon Szczecin",
        "away_team": "Legia Warszawa",
        "commence_time": kickoff.isoformat(),
        "family": "totals",
        "selection_key": "totals over 2 5",
        "point": 2.5,
    }
    second = {
        "home_team": "Legia Warszawa",
        "away_team": "Pogon Szczecin",
        "commence_time": kickoff.isoformat(),
        "family": "totals",
        "selection": "Больше 2.5",
        "point": 2.5,
    }

    assert patch._canonical_decision_key(first) == patch._canonical_decision_key(second)
    assert patch._canonical_row(first)["selection_key"] == "over"


def test_zero_remaining_daily_cap_never_selects_another_observation(monkeypatch) -> None:
    monkeypatch.setenv("FOCUSED_ALPHA_ACCUMULATION_DAILY_MAX", "2")
    snapshot = datetime.now(UTC).isoformat()
    selections = {
        "a": {"selected_at_utc": snapshot},
        "b": {"selected_at_utc": snapshot},
    }
    ranked = [
        {
            "decision_key": "c",
            "commence_time": (datetime.now(UTC) + timedelta(hours=2)).isoformat(),
        }
    ]

    chosen, rejected, limits = patch._bounded_choose(
        ranked,
        selections,
        set(),
        snapshot,
    )

    assert limits["daily_max"] == 2
    assert chosen == []
    assert rejected == {"daily_cap_reached": 1}
