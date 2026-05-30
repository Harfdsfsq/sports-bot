from __future__ import annotations

import json
import runpy
from pathlib import Path


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def base_inventory() -> dict:
    return {
        "date_local": "2026-05-30",
        "matches": [
            {
                "match_key": "soccer|alpha|beta|2026-05-30",
                "kickoff_utc": "2026-05-30T18:00:00+00:00",
                "league_name": "Test League",
                "home_team": "Alpha FC",
                "away_team": "Beta FC",
                "coverage": {"odds": True, "context": True},
                "price_confirmation_sources_count": 2,
                "odds_sources": ["odds_api_io"],
                "context_sources": ["sstats"],
            }
        ],
        "counts": {},
    }


def run_truth(tmp_path: Path) -> dict:
    cwd = Path.cwd()
    try:
        import os
        os.chdir(tmp_path)
        runpy.run_path(str(cwd / "scripts" / "build_day_inventory_coverage_truth.py"), run_name="__main__")
    except SystemExit as exc:
        assert exc.code in (0, None)
    finally:
        import os
        os.chdir(cwd)
    return json.loads((tmp_path / ".data/exports/latest-day-inventory-coverage-truth.json").read_text(encoding="utf-8"))


def test_progressive_sources_are_counted_and_written_back(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("DAY_INVENTORY_TARGET_DATE", "2026-05-30")
    write_json(tmp_path / ".data/day_inventory/2026-05-30.json", base_inventory())
    write_json(tmp_path / ".data/exports/latest-context-source-index.json", {
        "by_match": {"soccer|alpha|beta|2026-05-30": ["highlightly"]}
    })
    write_json(tmp_path / ".data/day_inventory/progressive_coverage_state.json", {
        "date_local": "2026-05-30",
        "matches": {
            "soccer|alpha|beta|2026-05-30": {
                "odds_sources": ["odds_api_io", "bzzoiro"],
                "context_sources": ["sstats", "bzzoiro"],
            }
        },
    })

    report = run_truth(tmp_path)
    assert report["counts"]["matches_with_2plus_odds_sources"] == 1
    assert report["counts"]["matches_with_2plus_context_sources"] == 1
    inv = json.loads((tmp_path / ".data/day_inventory/2026-05-30.json").read_text(encoding="utf-8"))
    row = inv["matches"][0]
    assert set(row["coverage"]["context_sources"]) >= {"sstats", "bzzoiro", "highlightly"}
    assert set(row["coverage"]["odds_sources"]) >= {"odds_api_io", "bzzoiro"}


def test_highwater_prevents_later_context_drop(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("DAY_INVENTORY_TARGET_DATE", "2026-05-30")
    inv = base_inventory()
    write_json(tmp_path / ".data/day_inventory/2026-05-30.json", inv)
    write_json(tmp_path / ".data/day_inventory/progressive_coverage_state.json", {
        "date_local": "2026-05-30",
        "matches": {
            "soccer|alpha|beta|2026-05-30": {
                "odds_sources": ["odds_api_io", "bzzoiro"],
                "context_sources": ["sstats", "bzzoiro"],
            }
        },
    })
    first = run_truth(tmp_path)
    assert first["counts"]["matches_with_2plus_context_sources"] == 1

    # Simulate a later zero/fresh-snapshot run that loses current context evidence.
    inv2 = base_inventory()
    inv2["matches"][0]["coverage"] = {"odds": True, "context": False}
    inv2["matches"][0]["context_sources"] = []
    write_json(tmp_path / ".data/day_inventory/2026-05-30.json", inv2)
    write_json(tmp_path / ".data/day_inventory/progressive_coverage_state.json", {
        "date_local": "2026-05-30", "matches": {}
    })
    second = run_truth(tmp_path)
    assert second["counts"]["matches_with_context"] == 1
    assert second["counts"]["matches_with_2plus_context_sources"] == 1
    assert second["counts"]["matches_ready_for_model"] == 1


def test_frozen_roster_keeps_same_match_set_when_current_inventory_changes(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("DAY_INVENTORY_TARGET_DATE", "2026-05-30")
    first = base_inventory()
    write_json(tmp_path / ".data/day_inventory/2026-05-30.json", first)
    run_truth(tmp_path)

    changed = {
        "date_local": "2026-05-30",
        "matches": [
            {
                "match_key": "soccer|gamma|delta|2026-05-30",
                "kickoff_utc": "2026-05-30T19:00:00+00:00",
                "league_name": "Other League",
                "home_team": "Gamma FC",
                "away_team": "Delta FC",
                "coverage": {"odds": True, "context": True},
                "price_confirmation_sources_count": 2,
                "odds_sources": ["odds_api_io"],
                "context_sources": ["sstats", "bzzoiro"],
            }
        ],
        "counts": {},
    }
    write_json(tmp_path / ".data/day_inventory/2026-05-30.json", changed)
    second = run_truth(tmp_path)
    keys = [row["match_key"] for row in second["rows"]]
    assert keys == ["soccer|alpha|beta|2026-05-30"]
    roster = second["frozen_roster"]
    assert roster["restored"] is True
    assert roster["overlap"] == 0
