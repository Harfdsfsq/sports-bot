from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from scripts import build_focused_alpha_decisions_v2 as decisions


def _candidate(kickoff: datetime, name: str) -> dict:
    return {
        "match_key": f"2026-07-24|{name}|opponent",
        "home_team": name,
        "away_team": "Opponent",
        "league_name": "League",
        "commence_time": kickoff.isoformat(),
        "family": "totals",
        "selection": "Меньше 2.5",
        "selection_key": "under",
        "point": 2.5,
        "odds": 2.0,
    }


def test_collect_candidates_rejects_stale_and_far_future_rows(
    tmp_path: Path,
    monkeypatch,
) -> None:
    now = datetime(2026, 7, 24, 10, tzinfo=UTC)
    path = tmp_path / "candidates.json"
    path.write_text(
        json.dumps(
            [
                _candidate(now - timedelta(hours=1), "Stale"),
                _candidate(now + timedelta(minutes=10), "Inside lead"),
                _candidate(now + timedelta(hours=5), "Fresh"),
                _candidate(now + timedelta(hours=50), "Far"),
                {
                    "match_key": "missing-time",
                    "home_team": "Missing",
                    "away_team": "Opponent",
                },
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(decisions, "CANDIDATE_PATHS", (path,))
    monkeypatch.setenv("MIN_KICKOFF_LEAD_MINUTES", "20")
    monkeypatch.setenv("HARIZON_DATA_COLLECTION_WINDOW_HOURS", "36")
    stats: dict[str, int] = {}

    rows = decisions.collect_candidates(now=now, stats=stats)

    assert [row["home_team"] for row in rows] == ["Fresh"]
    assert stats == {
        "raw_rows": 5,
        "missing_kickoff": 1,
        "inside_minimum_lead": 1,
        "stale_or_started": 1,
        "outside_data_horizon": 1,
        "eligible_rows": 1,
        "unique_rows": 1,
        "duplicates_collapsed": 0,
        "evidence_truth_repaired": 1,
    }


def test_build_decisions_reports_candidate_pool_filtering(
    tmp_path: Path,
    monkeypatch,
) -> None:
    now = datetime.now(UTC)
    path = tmp_path / "candidates.json"
    path.write_text(
        json.dumps(
            [
                _candidate(now - timedelta(days=20), "Old"),
                _candidate(now + timedelta(hours=3), "Current"),
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(decisions, "CANDIDATE_PATHS", (path,))
    monkeypatch.setattr(decisions, "OUT", tmp_path / "decisions.json")
    monkeypatch.setattr(
        decisions.base,
        "build_history_audit",
        lambda: {"live_learning_ready": False, "by_league": {}},
    )

    payload = decisions.build_decisions(now=now)

    assert payload["candidate_pool"]["raw_rows"] == 2
    assert payload["candidate_pool"]["stale_or_started"] == 1
    assert payload["candidate_pool"]["eligible_rows"] == 1
    assert payload["candidates_seen"] == 1
