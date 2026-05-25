from __future__ import annotations

import importlib.util
from pathlib import Path


def load(path: str):
    spec = importlib.util.spec_from_file_location(Path(path).stem, path)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


def test_candidate_opportunity_keys_normalize():
    mod = load('scripts/build_candidate_opportunity_audit.py')
    assert mod.norm('soccer|Team A|Team B|2026-05-25') == 'team a team b 2026 05 25'


def test_prediction_calibration_as_float():
    mod = load('scripts/build_prediction_calibration_audit.py')
    assert mod.as_float('1,25') == 1.25
    assert mod.as_float('') is None


def test_ledger_identity_stable():
    mod = load('scripts/update_prediction_ledger.py')
    row = {'match_key': 'soccer|A|B|2026', 'family': 'totals', 'selection': 'under', 'point': 2.5}
    assert mod.identity(row) == 'soccer a b 2026|totals|under|2.5'


def test_inventory_annotation_no_promotion_by_default(monkeypatch):
    mod = load('app/services/candidate_inventory_evidence_annotation_patch.py')
    monkeypatch.delenv('CANDIDATE_EVIDENCE_PROMOTE_TO_METRICS', raising=False)
    assert mod._bool_env('CANDIDATE_EVIDENCE_PROMOTE_TO_METRICS', False) is False
