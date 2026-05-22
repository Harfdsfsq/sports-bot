from __future__ import annotations

import importlib.util
import json
from pathlib import Path


def _load_module(root: Path):
    spec = importlib.util.spec_from_file_location(
        "publish_controlled_fallback_day_inventory_test",
        root / "scripts" / "publish_controlled_fallback.py",
    )
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _candidate(match_key: str = "soccer|not in inventory|team b|2026-05-22") -> dict:
    return {
        "match_key": match_key,
        "home_team": "Not In Inventory",
        "away_team": "Team B",
        "commence_time": "2026-05-22T21:45:00+00:00",
        "family": "totals",
        "selection": "Меньше",
        "point": 3.0,
        "odds": 2.0,
        "adjusted_probability": 0.60,
        "market_probability": 0.50,
        "books_count": 2,
        "sources_count": 1,
        "source_summary": {},
    }


def test_fallback_pool_rejects_rows_outside_day_inventory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DAY_INVENTORY_TARGET_DATE", "2026-05-22")
    monkeypatch.setenv("CONTROLLED_FALLBACK_REQUIRE_FRESH_ARTIFACTS", "false")
    monkeypatch.setenv("CONTROLLED_FALLBACK_FILTER_POOL_BY_TIME", "false")
    monkeypatch.setenv("CONTROLLED_FALLBACK_REQUIRE_DAY_INVENTORY_MEMBERSHIP", "true")

    _write_json(
        tmp_path / ".data/day_inventory/2026-05-22.json",
        {"matches": [{"match_key": "soccer|inventory team|team c|2026-05-22", "home_team": "Inventory Team", "away_team": "Team C", "kickoff_utc": "2026-05-22T21:00:00+00:00"}]},
    )
    _write_json(tmp_path / ".data/exports/latest-rescue-candidates.json", [_candidate()])
    _write_json(tmp_path / ".logs/debug-last-run.json", {"created_at": "2026-05-22T19:50:00+00:00"})

    module = _load_module(Path(__file__).resolve().parents[1])
    pool, counts = module.load_candidate_pool()

    assert pool == []
    assert counts["latest_rescue_candidates_not_in_day_inventory"] == 1
    assert counts["day_inventory_membership_keys"] > 0


def test_fallback_pool_keeps_rows_inside_day_inventory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DAY_INVENTORY_TARGET_DATE", "2026-05-22")
    monkeypatch.setenv("CONTROLLED_FALLBACK_REQUIRE_FRESH_ARTIFACTS", "false")
    monkeypatch.setenv("CONTROLLED_FALLBACK_FILTER_POOL_BY_TIME", "false")
    monkeypatch.setenv("CONTROLLED_FALLBACK_REQUIRE_DAY_INVENTORY_MEMBERSHIP", "true")

    match_key = "soccer|inventory team|team c|2026-05-22"
    _write_json(
        tmp_path / ".data/day_inventory/2026-05-22.json",
        {"matches": [{"match_key": match_key, "home_team": "Inventory Team", "away_team": "Team C", "kickoff_utc": "2026-05-22T21:00:00+00:00"}]},
    )
    row = _candidate(match_key)
    row["home_team"] = "Inventory Team"
    row["away_team"] = "Team C"
    _write_json(tmp_path / ".data/exports/latest-rescue-candidates.json", [row])
    _write_json(tmp_path / ".logs/debug-last-run.json", {"created_at": "2026-05-22T19:50:00+00:00"})

    module = _load_module(Path(__file__).resolve().parents[1])
    module._DAY_INVENTORY_MEMBERSHIP_CACHE = None
    pool, counts = module.load_candidate_pool()

    assert len(pool) == 1
    assert pool[0]["match_key"] == match_key
    assert counts["latest_rescue_candidates"] == 1
