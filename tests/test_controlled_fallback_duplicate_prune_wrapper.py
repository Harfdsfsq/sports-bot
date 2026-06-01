from pathlib import Path


def test_wrapper_prunes_sent_duplicates_before_eval_when_alternatives_exist() -> None:
    source = Path('scripts/publish_controlled_fallback_with_run_context.py').read_text(encoding='utf-8')
    assert 'load_candidate_pool_with_sent_duplicate_pruning' in source
    assert 'CONTROLLED_FALLBACK_PRUNE_SENT_DUPLICATES_BEFORE_EVAL' in source
    assert 'kept_original_pool_for_transparent_duplicate_report' in source
