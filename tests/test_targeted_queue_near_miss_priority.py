from pathlib import Path
import importlib.util


def load_queue_module():
    path = Path(__file__).resolve().parents[1] / 'app' / 'services' / 'targeted_enrichment_queue.py'
    spec = importlib.util.spec_from_file_location('targeted_enrichment_queue_test', path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_jsonl_rows_loader_reads_recent_near_misses(tmp_path):
    m = load_queue_module()
    path = tmp_path / 'near.jsonl'
    path.write_text('{"match_key":"soccer|a|b|2026-05-29", "ev_pct": 5}\nnot-json\n{"match_key":"soccer|c|d|2026-05-29", "ev_pct": 3}\n', encoding='utf-8')
    rows = m.load_jsonl_rows(path)
    assert [r['match_key'] for r in rows] == ['soccer|a|b|2026-05-29', 'soccer|c|d|2026-05-29']
