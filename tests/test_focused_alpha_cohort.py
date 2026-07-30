from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.services import focused_alpha


def _row(name: str, score_profile: str) -> dict:
    now = datetime(2026, 7, 24, tzinfo=UTC)
    row = {
        "match_key": f"2026-07-24|{name}|opponent",
        "home_team": name,
        "away_team": "Opponent",
        "league_name": "Senior League",
        "kickoff_utc": (now + timedelta(hours=10)).isoformat(),
        "hours_to_kickoff": 10.0,
        "provider_assignment_eligible": True,
        "ledger_identity_match": True,
        "metadata": {},
        "coverage": {},
        "source_ids": {},
    }
    if score_profile == "strong":
        row["metadata"] = {
            "verified_odds_sources": ["odds_api_io", "bzzoiro"],
            "verified_context_sources": ["sstats", "espn"],
        }
        row["verified_bookmakers"] = ["pinnacle", "betfair", "bet365"]
        row["expected_home"] = 1.4
        row["expected_away"] = 0.9
        row["home_form"] = ["W", "D", "W"]
        row["source_ids"] = {"odds_api_io": "1", "bzzoiro": "2", "sstats": "3", "espn": "4"}
    return row


def test_focus_cohort_selects_quality_not_fixed_volume(monkeypatch) -> None:
    monkeypatch.setenv("FOCUSED_ALPHA_MAX_MATCHES", "100")
    monkeypatch.setenv("FOCUSED_ALPHA_MIN_MATCH_SCORE", "44")
    monkeypatch.setenv("FOCUSED_ALPHA_EXPLORATION_SLOTS", "0")
    monkeypatch.setattr(focused_alpha, "atomic_write", lambda *args, **kwargs: None)
    history = {"live_learning_ready": False, "settled_rows": 0, "by_league": {}}
    rows = [_row("Strong", "strong")] + [_row(f"Weak-{index}", "weak") for index in range(20)]

    result = focused_alpha.select_focus_cohort(
        rows,
        now=datetime(2026, 7, 24, tzinfo=UTC),
        history_report=history,
    )

    selected = result["rows"]
    assert [row["home_team"] for row in selected] == ["Strong"]
    assert result["report"]["selected_rows"] == 1
    assert result["report"]["max_matches"] == 100
    assert result["report"]["fixed_coverage_quota"] is False
    assert result["report"]["publication_minimum_count"] == 0


def test_one_plus_one_match_receives_information_gain_priority() -> None:
    row = _row("Enrichable", "weak")
    row["metadata"] = {
        "verified_odds_sources": ["odds_api_io"],
        "verified_context_sources": ["sstats"],
    }
    detail = focused_alpha.score_match(row, {"live_learning_ready": False, "by_league": {}})

    assert detail["evidence"]["expected_enrichment_gain_score"] > 0
    assert "high_expected_enrichment_gain" in detail["reasons"]


def test_cold_start_selects_bounded_nearest_first_bootstrap(monkeypatch) -> None:
    monkeypatch.setenv("FOCUSED_ALPHA_MIN_MATCH_SCORE", "44")
    monkeypatch.setenv("FOCUSED_ALPHA_EXPLORATION_SLOTS", "0")
    monkeypatch.setenv("FOCUSED_ALPHA_BOOTSTRAP_MATCHES", "2")
    monkeypatch.setenv("FOCUSED_ALPHA_BOOTSTRAP_MAX_HOURS", "36")
    monkeypatch.setattr(focused_alpha, "atomic_write", lambda *args, **kwargs: None)
    history = {"live_learning_ready": False, "settled_rows": 0, "by_league": {}}
    rows = [
        _row("Far", "weak"),
        _row("Near", "weak"),
        _row("Middle", "weak"),
    ]
    rows[0]["hours_to_kickoff"] = 18.0
    rows[1]["hours_to_kickoff"] = 3.0
    rows[2]["hours_to_kickoff"] = 7.0

    result = focused_alpha.select_focus_cohort(
        rows,
        now=datetime(2026, 7, 24, tzinfo=UTC),
        history_report=history,
    )

    assert [row["home_team"] for row in result["rows"]] == ["Near", "Middle"]
    assert all(row["focused_alpha_bootstrap"] for row in result["rows"])
    assert result["report"]["quality_selected_rows"] == 0
    assert result["report"]["bootstrap_selected_rows"] == 2
    assert result["report"]["status"] == "cold_start_bootstrap_active"
