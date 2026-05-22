from __future__ import annotations

import json
from pathlib import Path


def test_progressive_active_core_excludes_zero_budget_sportlogic(tmp_path, monkeypatch):
    from app.services import progressive_active_core_budget_patch as patch

    root = tmp_path
    export = root / ".data" / "exports"
    export.mkdir(parents=True)
    (export / "latest-per-run-api-quota-contract.json").write_text(
        json.dumps({"per_run_grants": {"odds_api_io": 200, "bzzoiro": 100, "sportlogic": 0, "sstats": 100}}),
        encoding="utf-8",
    )
    (export / "latest-progressive-coverage-plan.json").write_text(
        json.dumps({"contract": {"core_odds_providers": ["bzzoiro", "odds_api_io", "sportlogic"], "core_context_providers": ["bzzoiro", "sstats"]}, "diagnostics": {}}),
        encoding="utf-8",
    )

    monkeypatch.setattr(patch, "ROOT", root)
    monkeypatch.setattr(patch, "EXPORT_DIR", export)
    monkeypatch.setattr(patch, "PLAN_PATH", export / "latest-progressive-coverage-plan.json")
    monkeypatch.setattr(patch, "CONTRACT_PATH", export / "latest-per-run-api-quota-contract.json")
    monkeypatch.setattr(patch, "REPORT_PATH", export / "latest-progressive-active-core-budget-patch.json")

    result = patch.patch_plan_file()
    plan = json.loads((export / "latest-progressive-coverage-plan.json").read_text(encoding="utf-8"))

    assert result["status"] == "applied"
    assert plan["contract"]["core_odds_providers"] == ["bzzoiro", "odds_api_io"]
    assert any(row["provider"] == "sportlogic" and row["reason"] == "zero_budget" for row in plan["contract"]["excluded_core_providers"])
