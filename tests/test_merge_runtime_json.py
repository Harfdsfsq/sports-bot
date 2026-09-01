from __future__ import annotations

import json
from pathlib import Path

from scripts import merge_runtime_json as merge_driver


def _row(index: int, *, books: int = 1) -> dict[str, object]:
    return {
        "match_key": f"soccer|home-{index}|away-{index}|2026-07-20",
        "home_team": f"Home {index}",
        "away_team": f"Away {index}",
        "kickoff_utc": "2026-07-20T12:00:00+00:00",
        "books_count": books,
        "coverage": {"odds": True},
    }


def test_merge_payloads_preserves_up_to_300_unique_inventory_rows() -> None:
    current = {
        "date_local": "2026-07-20",
        "updated_at_utc": "2026-07-19T20:00:00+00:00",
        "target_matches": 300,
        "matches": [_row(index) for index in range(180)],
    }
    other = {
        "date_local": "2026-07-20",
        "updated_at_utc": "2026-07-19T21:00:00+00:00",
        "target_matches": 300,
        "matches": [_row(index) for index in range(100, 280)],
    }

    merged = merge_driver.merge_payloads({}, current, other, ".data/day_inventory/current.json")

    assert len(merged["matches"]) == 280
    assert merged["counts"]["matches_total"] == 280
    assert merged["counts"]["runtime_json_merge_driver_applied"] is True
    assert merged["runtime_json_merge_driver"]["status"] == "merged"


def test_merge_payloads_keeps_richer_duplicate_row() -> None:
    current = {"matches": [_row(1, books=1)]}
    richer = _row(1, books=3)
    richer["context_sources"] = ["sstats", "bzzoiro"]
    other = {"matches": [richer]}

    merged = merge_driver.merge_payloads({}, current, other)

    assert len(merged["matches"]) == 1
    assert merged["matches"][0]["books_count"] == 3
    assert merged["matches"][0]["context_sources"] == ["sstats", "bzzoiro"]


def test_merge_driver_writes_valid_json_without_conflict_markers(tmp_path: Path) -> None:
    base = tmp_path / "base.json"
    current = tmp_path / "current.json"
    other = tmp_path / "other.json"
    base.write_text(json.dumps({"matches": []}), encoding="utf-8")
    current.write_text(json.dumps({"matches": [_row(1)]}), encoding="utf-8")
    other.write_text(json.dumps({"matches": [_row(2)]}), encoding="utf-8")

    assert merge_driver.main([str(base), str(current), str(other), "7", "current.json"]) == 0

    text = current.read_text(encoding="utf-8")
    payload = json.loads(text)
    assert len(payload["matches"]) == 2
    assert "<<<<<<<" not in text
    assert "=======" not in text
    assert ">>>>>>>" not in text
