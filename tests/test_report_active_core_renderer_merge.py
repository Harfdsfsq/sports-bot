from __future__ import annotations

import json
from pathlib import Path


def test_active_core_budget_artifact_shape(tmp_path):
    payload = {
        "active_core_odds_providers": ["bzzoiro", "odds_api_io"],
        "active_core_context_providers": ["bzzoiro", "sstats"],
        "excluded_core_providers": [{"provider": "sportlogic", "reason": "zero_budget"}],
    }
    path = tmp_path / "latest-progressive-active-core-budget-patch.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    data = json.loads(path.read_text(encoding="utf-8"))
    assert "sportlogic" not in data["active_core_odds_providers"]
    assert data["excluded_core_providers"][0]["reason"] == "zero_budget"
