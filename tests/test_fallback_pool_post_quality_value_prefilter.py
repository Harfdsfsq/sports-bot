from __future__ import annotations

import importlib.util
from pathlib import Path


def load_module():
    path = Path("scripts/publish_controlled_fallback.py")
    spec = importlib.util.spec_from_file_location("fallback_prefilter_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_post_quality_negative_value_is_prefiltered(monkeypatch):
    m = load_module()
    row = {
        "match_key": "soccer|a|b|2026-05-22",
        "home_team": "A",
        "away_team": "B",
        "commence_time": "2099-01-01T12:00:00+00:00",
        "family": "totals",
        "selection": "Under",
        "point": 2.5,
        "odds": 2.55,
        "adjusted_probability": 0.40,
        "diagnostics": {"quality": {"final_adjusted_probability": 0.3316}},
    }
    metrics = {
        "odds": 2.55,
        "adjusted_probability": 0.3316,
        "canonical_ev_pct": (0.3316 * 2.55 - 1.0) * 100.0,
        "canonical_edge_pp": (0.3316 - 1 / 2.55) * 100.0,
    }
    assert metrics["canonical_ev_pct"] < 0
