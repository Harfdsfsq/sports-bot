from pathlib import Path
import importlib.util
import json
import os
import sys


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_runtime_preflight_has_apply_phase_policy(monkeypatch, tmp_path):
    mod = load_module(Path("app/services/runtime_preflight.py"), "runtime_preflight_test")
    monkeypatch.setenv("RUNBOT_DISCOVERY_FIRST_PREPARE_ENABLED", "false")
    monkeypatch.setenv("LEGACY_RUNTIME_EXTENSIONS_ENABLED", "false")
    preflight = mod.RuntimePreflight(export_dir=tmp_path)
    report = preflight.apply_phase_policy()
    assert report["stage"] == "phase_policy"
    assert report["phase"] == "run-once"
    assert (tmp_path / "latest-runtime-phase-policy.json").exists()


def test_v9_runtime_error_detection(tmp_path, monkeypatch):
    v8_stub = Path("scripts/send_harizon_telegram_run_report_v8.py")
    if not v8_stub.exists():
        v8_stub.write_text(
            "from types import SimpleNamespace\n"
            "def build_payload(): return {'diagnostics': {}, 'funnel': {}}\n"
            "def render(payload): return '🧾 HARIZON run report v8\\n\\n📦 Покрытие\\n'\n"
            "v7=SimpleNamespace(v5=SimpleNamespace(OUT_V5_JSON='x.json', OUT_V5_TXT='x.txt', OUT_JSON='y.json', OUT_TXT='y.txt', write_json=lambda *a, **k: None, write_text=lambda *a, **k: None, send_telegram=lambda text: {'sent': False}))\n",
            encoding="utf-8",
        )
    mod = load_module(Path("scripts/send_harizon_telegram_run_report_v9.py"), "v9_runtime_error_test")
    monkeypatch.chdir(tmp_path)
    p = tmp_path / ".data" / "exports"
    p.mkdir(parents=True)
    (p / "latest-run-bot.log").write_text(
        "Traceback (most recent call last):\nAttributeError: 'RuntimePreflight' object has no attribute 'apply_phase_policy'\n",
        encoding="utf-8",
    )
    err = mod._runtime_error_from_log()
    assert err["reason"] == "runtime_preflight_apply_phase_policy_missing"
    block = mod._runtime_error_block({"diagnostics": {"runtime_error": err}})
    assert "Runtime error" in block
    assert "apply_phase_policy" in block


def test_prediction_ledger_records_runtime_error(tmp_path, monkeypatch):
    mod = load_module(Path("scripts/update_prediction_ledger.py"), "ledger_runtime_error_test")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GITHUB_RUN_ID", "123")
    p = tmp_path / ".data" / "exports"
    p.mkdir(parents=True)
    (p / "latest-run-bot.log").write_text(
        "Traceback (most recent call last):\nAttributeError: 'RuntimePreflight' object has no attribute 'apply_phase_policy'\n",
        encoding="utf-8",
    )
    mod.ROOT = tmp_path
    mod.EXPORT_DIR = p
    mod.LEDGER = tmp_path / ".data" / "prediction-ledger.jsonl"
    mod.SUMMARY = p / "latest-prediction-ledger-summary.json"
    mod.RUN_LOG = p / "latest-run-bot.log"
    rows = mod.collect_current_rows()
    assert rows and rows[0]["status"] == "runtime_error"
    assert "runtime_preflight_apply_phase_policy_missing" in rows[0]["reasons"]
