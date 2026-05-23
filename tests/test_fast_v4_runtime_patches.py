from __future__ import annotations

import importlib.util
from pathlib import Path


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_fast_budget_sets_account2_event_lookup() -> None:
    text = Path('scripts/apply_fast_run_budget.py').read_text(encoding='utf-8')
    assert "ODDS_API_IO_FAST_EVENT_ACCOUNT" in text
    assert "ODDS_API_IO_ACCOUNT_ORDER" in text
    assert "balanced-depth-v4" in text


def test_runtime_chain_installs_fast_and_bzzoiro_point_patches() -> None:
    text = Path('app/services/runtime_startup_chain.py').read_text(encoding='utf-8')
    fast_idx = text.index('app.providers.odds_api_io_fast_event_account_patch')
    exact_idx = text.index('app.services.bzzoiro_exact_offer_bridge_patch')
    point_idx = text.index('app.services.bzzoiro_total_point_normalization_patch')
    assert fast_idx < exact_idx
    assert point_idx < exact_idx


def test_bzzoiro_total_point_normalizer_scales_soccer_totals() -> None:
    mod = load_module(Path('app/services/bzzoiro_total_point_normalization_patch.py'), 'bzz_point_patch_test')
    patched, changed, before, after = mod._patch_hint({'family': 'totals', 'selection': 'Under', 'point': 25})
    assert changed is True
    assert before == 25.0
    assert after == 2.5
    assert patched['point'] == 2.5
    assert patched['line'] == 2.5
