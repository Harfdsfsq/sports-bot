from __future__ import annotations

import json
import os
import runpy
import shutil
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def copy_script(tmp_path: Path, name: str) -> None:
    target = tmp_path / "scripts" / name
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(REPO_ROOT / "scripts" / name, target)


def write_json(path: Path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_evidence_key_alias_repairs_runtime_candidate_to_inventory(tmp_path, monkeypatch):
    copy_script(tmp_path, "repair_inventory_evidence_key_aliases.py")
    copy_script(tmp_path, "build_day_inventory_coverage_truth.py")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DAY_INVENTORY_TARGET_DATE", "2026-05-24")
    inv = {
        "matches": [
            {
                "match_key": "2026-05-25|columbus crew 2|toronto",
                "kickoff_utc": "2026-05-25T00:00:00+00:00",
                "home_team": "Columbus Crew 2",
                "away_team": "Toronto FC II",
                "coverage": {"odds": True},
                "metadata": {"independent_odds_sources_count": 1},
                "odds_sources": ["odds_api_io"],
            }
        ]
    }
    write_json(tmp_path / ".data/day_inventory/2026-05-24.json", inv)
    write_json(
        tmp_path / ".data/exports/latest-api-coverage-consensus-runtime-patch.json",
        {
            "sample": [
                {
                    "match_key": "soccer|columbus crew 2|toronto 2|2026-05-25",
                    "home": "Columbus Crew 2",
                    "away": "Toronto FC II",
                    "exact_odds_sources": ["bzzoiro", "odds_api_io_account1"],
                    "exact_odds_sources_count": 2,
                    "exact_books_count": 2,
                }
            ]
        },
    )
    write_json(
        tmp_path / ".data/exports/latest-quality-consensus-safe-relief.json",
        {
            "sample": [
                {
                    "match_key": "soccer|columbus crew 2|toronto 2|2026-05-25",
                    "home": "Columbus Crew 2",
                    "away": "Toronto FC II",
                    "context_sources": 4,
                }
            ]
        },
    )

    try:
        runpy.run_path(str(Path.cwd() / "scripts/repair_inventory_evidence_key_aliases.py"), run_name="__main__")
    except SystemExit as exc:
        assert exc.code in (0, None)
    repaired = json.loads((tmp_path / ".data/day_inventory/2026-05-24.json").read_text(encoding="utf-8"))["matches"][0]

    assert repaired["metadata"]["independent_odds_sources_count"] == 2
    assert repaired["metadata"]["price_confirmation_sources_count"] >= 2
    assert repaired["metadata"]["context_sources_count"] == 4
    assert set(repaired["odds_sources"]) == {"bzzoiro", "odds_api_io"}


def test_coverage_truth_uses_metadata_counts(tmp_path, monkeypatch):
    copy_script(tmp_path, "build_day_inventory_coverage_truth.py")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DAY_INVENTORY_TARGET_DATE", "2026-05-24")
    write_json(
        tmp_path / ".data/day_inventory/2026-05-24.json",
        {
            "matches": [
                {
                    "match_key": "2026-05-25|columbus crew 2|toronto",
                    "kickoff_utc": "2026-05-25T00:00:00+00:00",
                    "home_team": "Columbus Crew 2",
                    "away_team": "Toronto FC II",
                    "coverage": {"odds": True, "context": True},
                    "metadata": {
                        "independent_odds_sources_count": 2,
                        "price_confirmation_sources_count": 2,
                        "context_sources_count": 4,
                    },
                    "odds_sources": ["odds_api_io", "bzzoiro"],
                    "context_sources": [],
                }
            ]
        },
    )
    try:
        runpy.run_path(str(Path.cwd() / "scripts/build_day_inventory_coverage_truth.py"), run_name="__main__")
    except SystemExit as exc:
        assert exc.code in (0, None)
    payload = json.loads((tmp_path / ".data/exports/latest-day-inventory-coverage-truth.json").read_text(encoding="utf-8"))

    assert payload["counts"]["matches_with_2plus_price_confirmations"] == 1
    assert payload["counts"]["matches_with_2plus_odds_sources"] == 1
    assert payload["counts"]["matches_with_2plus_context_sources"] == 1
    assert payload["counts"]["matches_ready_for_publish_strict"] == 1
