from __future__ import annotations

import importlib.util
import json
from pathlib import Path


def _load_module(root: Path):
    spec = importlib.util.spec_from_file_location(
        "publish_controlled_fallback_promotion_pool_test",
        root / "scripts" / "publish_controlled_fallback.py",
    )
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_fallback_pool_reads_b_cover_promotion_sample_when_rescue_is_empty(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CONTROLLED_FALLBACK_REQUIRE_FRESH_ARTIFACTS", "true")
    monkeypatch.setenv("CONTROLLED_FALLBACK_FILTER_POOL_BY_TIME", "false")
    monkeypatch.setenv("CONTROLLED_FALLBACK_REQUIRE_DAY_INVENTORY_MEMBERSHIP", "false")

    candidate = {
        "match_key": "soccer|home|away|2026-08-06",
        "home_team": "Home",
        "away_team": "Away",
        "commence_time": "2026-08-06T18:00:00+00:00",
        "family": "totals",
        "selection": "Over 2.5",
        "point": 2.5,
        "odds": 1.9,
        "books_count": 2,
        "odds_sources_count": 1,
        "confirmation_sources_count": 1,
    }
    created_at = "2026-08-06T12:00:00+00:00"
    _write_json(tmp_path / ".data/exports/latest-run-summary.json", {"created_at": created_at, "status": "ok"})
    _write_json(tmp_path / ".data/exports/latest-rescue-candidates.json", [])
    _write_json(
        tmp_path / ".data/exports/latest-b-cover-value-promotion.json",
        {"created_at_utc": created_at, "status": "ok", "sample": [candidate]},
    )

    module = _load_module(Path(__file__).resolve().parents[1])
    pool, counts = module.load_candidate_pool()

    assert len(pool) == 1
    assert pool[0]["match_key"] == candidate["match_key"]
    assert pool[0]["_candidate_source"] == "b_cover_value_promotion"
    assert counts["b_cover_value_promotion"] == 1
