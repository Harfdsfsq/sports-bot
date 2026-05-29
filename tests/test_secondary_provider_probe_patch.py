from pathlib import Path
import importlib.util


def load_probe_module():
    path = Path(__file__).resolve().parents[1] / 'scripts' / 'probe_targeted_secondary_sources.py'
    spec = importlib.util.spec_from_file_location('probe_targeted_secondary_sources_test', path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_highlightly_nested_names_are_matched():
    m = load_probe_module()
    rows = [{'homeTeam': {'name': 'Cork City FC'}, 'awayTeam': {'name': 'Finn Harps FC'}}]
    targets = [{'home_team': 'Cork City FC', 'away_team': 'Finn Harps FC'}]
    assert m.fuzzy_count(rows, targets) == 1


def test_target_dates_use_candidate_kickoffs():
    m = load_probe_module()
    targets = [
        {'home_team': 'A', 'away_team': 'B', 'kickoff_utc': '2026-05-30T00:00:00+00:00'},
        {'home_team': 'C', 'away_team': 'D', 'commence_time': '2026-05-29T21:00:00+00:00'},
    ]
    assert m.target_dates(targets) == ['2026-05-30', '2026-05-29']


def test_response_rows_unwraps_common_provider_payloads():
    m = load_probe_module()
    assert m.response_rows({'data': [{'id': 1}]}) == [{'id': 1}]
    assert m.response_rows({'response': [{'id': 2}]}) == [{'id': 2}]
    assert m.response_rows({'result': [{'id': 3}]}) == [{'id': 3}]
