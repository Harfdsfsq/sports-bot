from __future__ import annotations

import importlib.util
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_controlled_fallback_b_tier_allows_single_bookmaker_contract(monkeypatch):
    mod = _load_module(Path("scripts/publish_controlled_fallback.py"), "publish_controlled_fallback_test")
    monkeypatch.setenv("CONTROLLED_FALLBACK_TIER_B_MIN_BOOKS", "1")
    monkeypatch.setenv("CONTROLLED_FALLBACK_TIER_B_MIN_CONTEXT_SOURCES", "2")
    monkeypatch.setenv("CONTROLLED_FALLBACK_TIER_B_MIN_CONFIRMATION_SOURCES", "2")
    monkeypatch.setenv("CONTROLLED_FALLBACK_TIER_B_REQUIRE_ODDS_SOURCES", "false")
    monkeypatch.setenv("CONTROLLED_FALLBACK_TIER_B_REQUIRE_2_BOOKS_FOR_TELEGRAM", "false")
    monkeypatch.setenv("CONTROLLED_FALLBACK_TIER_B_REQUIRE_INDEPENDENT_SOURCES", "false")
    monkeypatch.setenv("CONTROLLED_FALLBACK_REQUIRE_LINE_MOVEMENT_FOR_TELEGRAM", "false")
    monkeypatch.setenv("CONTROLLED_FALLBACK_PROXY_SINGLE_SOURCE_STRICT", "false")
    monkeypatch.setenv("CONTROLLED_FALLBACK_FINAL_MIN_EDGE_PP", "1.0")
    monkeypatch.setenv("CONTROLLED_FALLBACK_FINAL_MIN_EV_PCT", "2.0")

    candidate = {
        "family": "spreads",
        "bookmaker": "testbook",
        "raw_bucket_offers": [{"bookmaker": "testbook", "price": 1.91}],
    }
    metrics = {
        "odds": 1.91,
        "books_count": 1,
        "odds_sources_count": 0,
        "sources_count": 2,
        "confirmation_sources_count": 2,
        "confirmation_sources": ["sstats", "bzzoiro"],
        "confidence": 72.0,
        "quality_score": 74.0,
        "quality_score_source": "raw",
        "publication_score": 25.0,
        "canonical_edge_pp": 2.5,
        "canonical_ev_pct": 4.8,
        "quality_reasons": [],
    }

    tier_reasons = mod.tier_reasons("B", candidate, dict(metrics))
    assert not any("books_below" in reason or "odds_sources_below" in reason for reason in tier_reasons)

    final_reasons = mod.final_publish_guard_reasons(candidate, dict(metrics), "B")
    assert "telegram_publish_books_guard" not in final_reasons
    assert not any(reason.startswith("controlled_fallback_confirmation_sources_below_min") for reason in final_reasons)


def test_line_movement_key_is_stable_for_russian_english_totals(monkeypatch, tmp_path):
    mod = _load_module(Path("app/services/line_movement_state.py"), "line_movement_state_test")
    monkeypatch.setenv("LINE_MOVEMENT_STATE_PATH", str(tmp_path / "line.json"))
    monkeypatch.setenv("LINE_MOVEMENT_USE_SCHEDULED_CRON", "false")
    monkeypatch.setenv("LINE_MOVEMENT_MIN_RECHECK_MINUTES", "60")

    now = datetime(2026, 6, 10, 10, 0, tzinfo=timezone.utc)
    kickoff = now + timedelta(hours=5)
    base = {
        "canonical_match_id": "match-1",
        "family": "totals",
        "point": 2.5,
        "odds": 2.10,
        "ev_pct": 8.0,
        "edge_pct": 4.0,
        "books_count": 1,
        "commence_time": kickoff.isoformat(),
    }
    first = dict(base, selection="Under 2.5")
    second = dict(base, selection="Меньше 2.5", odds=2.06, ev_pct=7.0, edge_pct=3.5)

    out1 = mod.evaluate_and_record_line_movement(first, object(), now=now)
    out2 = mod.evaluate_and_record_line_movement(second, object(), now=now + timedelta(minutes=130))

    assert out1["status"] == "awaiting_next_run"
    assert out2["status"] == "movement_confirmed"
    assert out1["line_key"] == out2["line_key"]


def test_day_inventory_guard_restores_larger_snapshot(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    module_path = Path(os.environ.get("PATCH_BUILD_ROOT", ".")) / "scripts" / "guard_day_inventory_no_shrink.py"
    if not module_path.exists():
        module_path = Path(__file__).resolve().parents[1] / "scripts" / "guard_day_inventory_no_shrink.py"
    mod = _load_module(module_path, "guard_day_inventory_no_shrink_test")
    monkeypatch.setenv("DAY_INVENTORY_TARGET_DATE", "2026-06-10")

    day_dir = tmp_path / ".data" / "day_inventory"
    day_dir.mkdir(parents=True)
    big = {"date_local": "2026-06-10", "matches": [{"id": i} for i in range(300)], "counts": {"matches_total": 300}}
    small = {"date_local": "2026-06-10", "matches": [{"id": i} for i in range(160)], "counts": {"matches_total": 160}}
    (day_dir / "current.json").write_text(json.dumps(big), encoding="utf-8")
    snap = mod.snapshot()
    assert snap["best_matches"] == 300
    (day_dir / "current.json").write_text(json.dumps(small), encoding="utf-8")
    repaired = mod.repair()
    assert repaired["status"] == "repaired_shrunk_inventory"
    restored = json.loads((day_dir / "current.json").read_text(encoding="utf-8"))
    assert len(restored["matches"]) == 300
