from pathlib import Path
import importlib.util


def load_module(path: Path):
    spec = importlib.util.spec_from_file_location('day_inventory_cumulative_coverage', path)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


def test_secondary_probe_and_merge_run_before_coverage_truth(monkeypatch):
    mod = load_module(Path('scripts/day_inventory_cumulative_coverage.py'))
    calls = []

    def fake_run(path):
        calls.append(path.name)
        return {'path': str(path), 'status': 'ok'}

    monkeypatch.setattr(mod, 'run_python_script', fake_run)
    monkeypatch.setenv('TARGETED_SECONDARY_CONTEXT_PRE_COVERAGE_ENABLED', 'true')

    steps = mod.ensure_latest_run_coverage_merged()

    assert [step['status'] for step in steps]
    assert calls == [
        'match_data_coverage_report.py',
        'merge_run_coverage_into_day_inventory.py',
        'repair_inventory_source_counts.py',
        'probe_targeted_secondary_sources.py',
        'merge_targeted_secondary_context.py',
        'build_day_inventory_coverage_truth.py',
    ]


def test_secondary_precoverage_can_be_disabled(monkeypatch):
    mod = load_module(Path('scripts/day_inventory_cumulative_coverage.py'))
    calls = []

    monkeypatch.setattr(mod, 'run_python_script', lambda path: calls.append(path.name) or {'path': str(path), 'status': 'ok'})
    monkeypatch.setenv('TARGETED_SECONDARY_CONTEXT_PRE_COVERAGE_ENABLED', 'false')

    mod.ensure_latest_run_coverage_merged()

    assert 'probe_targeted_secondary_sources.py' not in calls
    assert 'merge_targeted_secondary_context.py' not in calls
    assert calls[-1] == 'build_day_inventory_coverage_truth.py'
