from __future__ import annotations

import importlib
import json
import sys
import types
from pathlib import Path


def test_services_package_import_has_no_runtime_patch_side_effects(tmp_path, monkeypatch):
    """Report/fallback helper processes import app.services while reading artifacts.

    The package import itself must not install CandidateFactory wrappers or write
    installer artifacts; runtime_startup_chain is the explicit production hook.
    """
    marker = tmp_path / "latest-post-integrity-candidate-rescue.json"
    monkeypatch.setenv("PYTHONPATH", str(Path.cwd()))
    sys.modules.pop("app.services", None)
    module = importlib.import_module("app.services")
    assert getattr(module, "__all__", []) == []
    assert not marker.exists()


def test_post_integrity_install_does_not_overwrite_existing_run_report(tmp_path, monkeypatch):
    from app.services import post_integrity_candidate_rescue as rescue

    report = tmp_path / "latest-post-integrity-candidate-rescue.json"
    report.write_text(json.dumps({"stage": "rescued", "returned": 3}), encoding="utf-8")
    monkeypatch.setattr(rescue, "REPORT_PATH", report)

    class CandidateFactory:
        _harizon_post_integrity_candidate_rescue_patch = True

    fake_model = types.SimpleNamespace(CandidateFactory=CandidateFactory)
    fake_controlled = types.SimpleNamespace(_build_rescue=lambda *args, **kwargs: ([], []))
    fake_integrity = types.SimpleNamespace(filter_candidates=lambda rows, rejections=None: rows)
    monkeypatch.setitem(sys.modules, "app.services.model", fake_model)
    monkeypatch.setitem(sys.modules, "app.services.controlled_candidate_rescue", fake_controlled)
    monkeypatch.setitem(sys.modules, "app.services.market_integrity", fake_integrity)

    result = rescue.install()

    assert result["status"] == "already_installed"
    assert json.loads(report.read_text(encoding="utf-8")) == {"stage": "rescued", "returned": 3}


def test_controlled_rescue_appends_when_model_pool_is_not_empty(monkeypatch):
    import app.services as services_pkg
    from app.services import controlled_candidate_rescue as rescue

    base_candidate = types.SimpleNamespace(
        match_key="match-1",
        family="totals",
        selection_key="totals|over|2.5|",
        point=2.5,
        team_side=None,
        reasons=[],
        source_summary={},
        publication_score=10.0,
        ev_pct=1.0,
        confidence=60.0,
    )
    rescue_candidate = types.SimpleNamespace(
        match_key="match-2",
        family="totals",
        selection_key="totals|under|3.5|",
        point=3.5,
        team_side=None,
        reasons=[],
        source_summary={},
        publication_score=80.0,
        ev_pct=8.0,
        confidence=72.0,
    )

    class CandidateFactory:
        def build_candidates(self, *args, **kwargs):
            return [base_candidate], {}, {"matches": []}

        def _filter_and_rank(self, candidates, rejections):
            return list(candidates)

    fake_model = types.SimpleNamespace(CandidateFactory=CandidateFactory)
    monkeypatch.setattr(services_pkg, "model", fake_model, raising=False)
    monkeypatch.setitem(sys.modules, "app.services.model", fake_model)
    monkeypatch.setattr(rescue, "_build_rescue", lambda *args, **kwargs: ([rescue_candidate], [{"match_key": "match-2"}]))
    monkeypatch.setenv("CONTROLLED_CONSENSUS_CANDIDATE_RESCUE_ENABLED", "true")
    monkeypatch.setenv("CONTROLLED_RESCUE_APPEND_TO_EXISTING_CANDIDATES", "true")

    result = rescue.install()
    candidates, rejections, debug = CandidateFactory().build_candidates([], {"match-2": []}, {}, None)

    assert result["status"] == "installed"
    assert candidates == [base_candidate, rescue_candidate]
    assert rejections["controlled_rescue_candidates_appended"] == 1
    assert debug["controlled_consensus_rescue"]["mode"] == "append"
    assert rescue_candidate.source_summary["controlled_rescue_append"] is True
