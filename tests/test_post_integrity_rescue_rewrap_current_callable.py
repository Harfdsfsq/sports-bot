from pathlib import Path


def test_post_integrity_rescue_uses_current_callable_marker():
    text = Path('app/services/post_integrity_candidate_rescue.py').read_text(encoding='utf-8')
    assert 'getattr(original, "_harizon_post_integrity_candidate_rescue_patch", False)' in text
    assert 'cls._harizon_post_integrity_candidate_rescue_patch' in text
    assert 'build_candidates_patched._harizon_post_integrity_candidate_rescue_patch = True' in text
    assert 'rewrapped_after_chain_overwrite' in text
