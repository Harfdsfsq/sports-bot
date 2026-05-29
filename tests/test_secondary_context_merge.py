from __future__ import annotations

import importlib.util
import json
from pathlib import Path


def load_module(path: Path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_merge_adds_highlightly_context_source(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / '.data/exports').mkdir(parents=True)
    probe = {
        'status': 'ok',
        'providers': {
            'highlightly': {
                'matched_contexts': [
                    {'match_key': 'soccer|cerezo osaka|tokyo|2026-05-30', 'provider': 'highlightly', 'provider_event_id': 123}
                ]
            }
        }
    }
    index = {'status': 'ok', 'by_match': {'soccer|cerezo osaka|tokyo|2026-05-30': ['sstats']}, 'source_counts': {'sstats': 1}}
    (tmp_path / '.data/exports/latest-targeted-secondary-provider-probe.json').write_text(json.dumps(probe), encoding='utf-8')
    (tmp_path / '.data/exports/latest-context-source-index.json').write_text(json.dumps(index), encoding='utf-8')

    module = load_module(Path(__file__).resolve().parents[1] / 'scripts' / 'merge_targeted_secondary_context.py')
    assert module.main() == 0

    merged = json.loads((tmp_path / '.data/exports/latest-context-source-index.json').read_text(encoding='utf-8'))
    assert merged['by_match']['soccer|cerezo osaka|tokyo|2026-05-30'] == ['highlightly', 'sstats']
    assert merged['source_counts']['highlightly'] == 1
    report = json.loads((tmp_path / '.data/exports/latest-targeted-secondary-context-merge.json').read_text(encoding='utf-8'))
    assert report['additions'] == 1
    assert report['context_2plus_after'] == report['context_2plus_before'] + 1


def test_probe_matches_highlightly_nested_team_names():
    module = load_module(Path(__file__).resolve().parents[1] / 'scripts' / 'probe_targeted_secondary_sources.py')
    targets = [{'home_team': 'Cerezo Osaka', 'away_team': 'FC Tokyo', 'commence_time': '2026-05-30T06:00:00+00:00'}]
    rows = [{'id': 777, 'homeTeam': {'name': 'Cerezo Osaka'}, 'awayTeam': {'name': 'FC Tokyo'}, 'date': '2026-05-30T06:00:00.000Z'}]
    contexts = module.matched_contexts(rows, targets, 'highlightly')
    assert len(contexts) == 1
    assert contexts[0]['provider'] == 'highlightly'
    assert contexts[0]['match_key'] == 'soccer|cerezo osaka|fc tokyo|2026-05-30'
