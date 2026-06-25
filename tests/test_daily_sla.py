from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.services.daily_sla import DailySlaThresholds, build_daily_sla_report, kickoff_bucket, match_coverage_status


def test_required_time_buckets() -> None:
    now = datetime(2026, 6, 25, 9, 0, tzinfo=UTC)
    assert kickoff_bucket(now + timedelta(hours=1), now) == "0-4h"
    assert kickoff_bucket(now + timedelta(hours=6), now) == "4-8h"
    assert kickoff_bucket(now + timedelta(hours=10), now) == "8-12h"
    assert kickoff_bucket(now + timedelta(hours=14), now) == "12-16h"
    assert kickoff_bucket(now + timedelta(hours=18), now) == "16-20h"
    assert kickoff_bucket(now + timedelta(hours=22), now) == "20-24h"
    assert kickoff_bucket(now + timedelta(hours=30), now) == ">24h"


def test_coverage_ready_requires_sources_books_and_movement() -> None:
    status = match_coverage_status({"coverage": {"odds_sources": ["a", "b"], "context_sources": ["c", "d"], "books": ["x", "y"], "line_snapshots_count": 2}}, DailySlaThresholds(target_matches=1))
    assert status["coverage_ready"] is True


def test_daily_sla_breaches_when_inventory_is_short() -> None:
    report = build_daily_sla_report({"matches": [{"coverage": {"odds_sources_count": 2, "context_sources_count": 1, "books_count": 2}}]}, thresholds=DailySlaThresholds(target_matches=3))
    assert report["summary"]["inventory_count"] == 1
    assert any(x.startswith("inventory_count") for x in report["breaches"])
