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
