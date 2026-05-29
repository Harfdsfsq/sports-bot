from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone

from app.services import targeted_enrichment_queue as q


@dataclass
class MatchStub:
    match_key: str
    sport_key: str = "soccer"
    commence_time: datetime = datetime(2026, 5, 29, 12, 0, tzinfo=timezone.utc)
    metadata: dict | None = None


def test_load_waiting_line_movement_from_truth_and_lifecycle(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DAY_INVENTORY_TARGET_DATE", "2026-05-29")
    (tmp_path / ".data/exports").mkdir(parents=True)
    (tmp_path / ".data/exports/latest-day-inventory-coverage-truth.json").write_text(
        json.dumps({
            "rows": [
                {"match_key": "soccer|a|b|2026-05-29", "line_movement_waiting": True},
                {"match_key": "soccer|old|b|2026-05-28", "line_movement_waiting": True},
            ]
        }),
        encoding="utf-8",
    )
    (tmp_path / ".data/candidate-lifecycle-state.json").write_text(
        json.dumps({
            "candidates": {
                "x": {
                    "match_key": "soccer|c|d|2026-05-29",
                    "line_movement_status": "awaiting_next_run",
                    "kickoff_utc": "2026-05-29T14:00:00+00:00",
                },
                "y": {
                    "match_key": "soccer|stale|d|2026-05-26",
                    "line_movement_status": "awaiting_next_run",
                    "kickoff_utc": "2026-05-26T14:00:00+00:00",
                },
            }
        }),
        encoding="utf-8",
    )
    keys = q.load_waiting_line_movement_keys()
    assert "soccer|a|b|2026-05-29" in keys
    assert "soccer|c|d|2026-05-29" in keys
    assert "soccer|stale|d|2026-05-26" not in keys


def test_context_source_index_by_match_is_counted(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".data/exports").mkdir(parents=True)
    (tmp_path / ".data/exports/latest-context-source-index.json").write_text(
        json.dumps({"by_match": {"soccer|a|b|2026-05-29": ["sstats", "clubelo"]}}),
        encoding="utf-8",
    )
    assert q.load_context_counts()["soccer|a|b|2026-05-29"] == 2


def test_select_report_counts_waiting_intersection(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DAY_INVENTORY_TARGET_DATE", "2026-05-29")
    (tmp_path / ".data/exports").mkdir(parents=True)
    (tmp_path / ".data/exports/latest-day-inventory-coverage-truth.json").write_text(
        json.dumps({"rows": [{"match_key": "soccer|a|b|2026-05-29", "line_movement_waiting": True}]}),
        encoding="utf-8",
    )
    selected, report = q.select_for_provider([MatchStub("soccer|a|b|2026-05-29")], "sstats", base_limit=10)
    assert len(selected) == 1
    assert report["waiting_line_items"] == 1
