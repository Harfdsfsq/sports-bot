from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.services.daily_sla import DailySlaThresholds, build_daily_sla_report, kickoff_bucket, match_coverage_status


def test_kickoff_bucket_uses_required_four_hour_windows() -> None:
    now = datetime(2026, 6, 25, 9, 0, tzinfo=UTC)
    assert kickoff_bucket(now + timedelta(hours=1), now) == "0-4h"
    assert kickoff_bucket(now + timedelta(hours=6), now) == "4-8h"
    assert kickoff_bucket(now + timedelta(hours=10), now) == "8-12h"
    assert kickoff_bucket(now + timedelta(hours=14), now) == "12-16h"
    assert kickoff_bucket(now + timedelta(hours=18), now) == "16-20h"
    assert kickoff_bucket(now + timedelta(hours=22), now) == "20-24h"
    assert kickoff_bucket(now + timedelta(hours=30), now) == ">24h"


def test_match_coverage_requires_two_odds_two_context_books_and_line_movement() -> None:
    row = {
        "match_key": "2026-06-25_team_a_team_b",
        "kickoff_utc": "2026-06-25T18:00:00Z",
        "coverage": {
            "odds_sources": ["odds_api_io", "sportlogic"],
            "context_sources": ["bzzoiro", "sstats"],
            "books": ["Bet365", "Unibet"],
            "line_snapshots_count": 2,
        },
    }
    status = match_coverage_status(row, DailySlaThresholds(target_matches=1))
    assert status["coverage_ready"] is True
    assert status["odds_sources_count"] == 2
    assert status["context_sources_count"] == 2
    assert status["books_count"] == 2


def test_daily_sla_report_flags_inventory_and_context_breaches() -> None:
    payload = {
        "matches": [
            {"match_key": "a", "coverage": {"odds_sources_count": 2, "context_sources_count": 2, "books_count": 2, "line_snapshots_count": 2}},
            {"match_key": "b", "coverage": {"odds_sources_count": 2, "context_sources_count": 1, "books_count": 2, "line_snapshots_count": 1}},
        ]
    }
    report = build_daily_sla_report(
        payload,
        thresholds=DailySlaThresholds(target_matches=3, offer_coverage_warn_pct=0.90, context_coverage_warn_pct=0.90),
    )
    assert report["summary"]["inventory_count"] == 2
    assert report["summary"]["coverage_ready_count"] == 1
    assert any(item.startswith("inventory_count") for item in report["breaches"])
    assert any(item.startswith("context_2plus_pct") for item in report["breaches"])
