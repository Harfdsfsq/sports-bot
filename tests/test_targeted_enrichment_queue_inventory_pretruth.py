from __future__ import annotations

import importlib.util
import json
from pathlib import Path


def _load_module(path: Path):
    spec = importlib.util.spec_from_file_location("targeted_enrichment_queue_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


class DummyMatch:
    sport_key = "soccer"

    def __init__(self, match_key: str, home: str, away: str, kickoff: str):
        self.match_key = match_key
        self.home_team = home
        self.away_team = away
        self.commence_time = kickoff
        self.metadata = {}


def test_queue_reads_waiting_and_context_from_day_inventory_before_truth_exists(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DAY_INVENTORY_TARGET_DATE", "2026-05-29")
    inv_dir = tmp_path / ".data" / "day_inventory"
    inv_dir.mkdir(parents=True)
    payload = {
        "date_local": "2026-05-29",
        "matches": [
            {
                "match_key": "soccer|alpha|beta|2026-05-29",
                "home_team": "Alpha",
                "away_team": "Beta",
                "kickoff_utc": "2026-05-29T12:00:00+00:00",
                "coverage": {
                    "odds": True,
                    "context": True,
                    "odds_sources": ["odds_api_io"],
                    "context_sources": ["sstats"],
                    "books_count": 2,
                },
                "books": ["bet365", "unibet"],
            }
        ],
    }
    (inv_dir / "2026-05-29.json").write_text(json.dumps(payload), encoding="utf-8")

    module = _load_module(Path(__file__).parents[1] / "app" / "services" / "targeted_enrichment_queue.py")
    waiting = module.load_waiting_line_movement_keys()
    contexts = module.load_context_counts()
    assert "soccer|alpha|beta|2026-05-29" in waiting
    assert contexts["soccer|alpha|beta|2026-05-29"] == 1

    match = DummyMatch("soccer|alpha|beta|2026-05-29", "Alpha", "Beta", "2026-05-29T12:00:00+00:00")
    selected, report = module.select_for_provider([match], "bzzoiro", {})
    assert selected == [match]
    assert report["waiting_line_items"] == 1
    assert report["context_index_items_in_pool"] == 1
