from __future__ import annotations

import importlib.util
import json
from pathlib import Path


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_v9_ignores_nonfatal_discovery_runtime_warning(tmp_path, monkeypatch):
    exports = tmp_path / ".data" / "exports"
    logs = tmp_path / ".logs"
    exports.mkdir(parents=True)
    logs.mkdir(parents=True)
    (exports / "latest-run-bot.log").write_text(
        "runbot discovery-first prepare step failed: x: RuntimeError: asyncio.run() cannot be called from a running event loop\n",
        encoding="utf-8",
    )
    (exports / "latest-controlled-fallback-report.json").write_text(
        json.dumps({"candidates_seen": 3, "evaluated": [{"match_key": "m"}]}),
        encoding="utf-8",
    )
    (logs / "debug-last-run.json").write_text(json.dumps({"summary": {"matches_seen": 184}}), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    # Copy only the tested function dependencies by loading the real script from the repository checkout.
    root = Path.cwd().parent
    script = root / "scripts" / "send_harizon_telegram_run_report_v9.py"
    if not script.exists():
        script = Path(__file__).resolve().parents[1] / "scripts" / "send_harizon_telegram_run_report_v9.py"
    mod = _load_module(script, "v9_nonfatal_test")
    assert mod._runtime_error_from_log() == {}


def test_ledger_ignores_nonfatal_discovery_runtime_warning(tmp_path, monkeypatch):
    exports = tmp_path / ".data" / "exports"
    exports.mkdir(parents=True)
    (exports / "latest-run-bot.log").write_text(
        "runbot discovery-first prepare step failed: x: RuntimeError: asyncio.run() cannot be called from a running event loop\n",
        encoding="utf-8",
    )
    (exports / "latest-controlled-fallback-report.json").write_text(
        json.dumps({"candidates_seen": 1, "evaluated": [{"match_key": "m"}]}),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    script = Path(__file__).resolve().parents[1] / "scripts" / "update_prediction_ledger.py"
    mod = _load_module(script, "ledger_nonfatal_test")
    assert mod.runtime_error_row() is None
