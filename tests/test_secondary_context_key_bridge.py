import json
import runpy
from pathlib import Path


def test_secondary_context_writes_canonical_bridge_keys(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    out = tmp_path / '.data' / 'exports'
    out.mkdir(parents=True)
    (out / 'latest-context-source-index.json').write_text(json.dumps({'by_match': {}}), encoding='utf-8')
    (out / 'latest-targeted-secondary-provider-probe.json').write_text(json.dumps({
        'status': 'ok',
        'providers': {
            'highlightly': {
                'matched_contexts': [{
                    'match_key': 'soccer|chattanooga|orlando city b|2026-05-31',
                    'home_team': 'Orlando City B',
                    'away_team': 'Chattanooga FC',
                    'kickoff_utc': '2026-05-31T23:00:00+00:00',
                    'provider': 'highlightly',
                }]
            }
        }
    }), encoding='utf-8')
    script = Path(__file__).resolve().parents[1] / 'scripts' / 'merge_targeted_secondary_context.py'
    try:
        runpy.run_path(str(script), run_name='__main__')
    except SystemExit as exc:
        assert exc.code in (0, None)
    index = json.loads((out / 'latest-context-source-index.json').read_text(encoding='utf-8'))
    by = index['by_match']
    assert 'soccer|chattanooga|orlando city b|2026-05-31' in by
    assert 'soccer|orlando_city_b|chattanooga_fc|2026-05-31' in by
    assert 'soccer|chattanooga_fc|orlando_city_b|2026-05-31' in by
    assert by['soccer|orlando_city_b|chattanooga_fc|2026-05-31'] == ['highlightly']
    report = json.loads((out / 'latest-targeted-secondary-context-merge.json').read_text(encoding='utf-8'))
    assert report['additions'] == 1
    assert report['bridge_additions'] >= 2
