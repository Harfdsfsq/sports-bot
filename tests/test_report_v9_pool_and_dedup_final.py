from scripts.send_harizon_telegram_run_report_v9 import is_real_pool_filter, pool_filter_counts
from app.services.candidate_factory_output_dedup_patch import dedupe_candidates


class C:
    def __init__(self, match_key, family="totals", selection="over", point=2.5, ev=0):
        self.match_key = match_key
        self.family = family
        self.selection = selection
        self.point = point
        self.ev_pct = ev
        self.edge_pct = ev
        self.confidence = ev
        self.quality_score = ev
        self.publication_score = ev


def test_v9_does_not_treat_source_pool_as_filter():
    assert not is_real_pool_filter("debug_candidates_before_quality")
    assert not is_real_pool_filter("latest_rescue_candidates")
    assert not is_real_pool_filter("day_inventory_membership_keys")
    assert is_real_pool_filter("debug_candidates_before_quality_stale_or_outside_window")
    assert is_real_pool_filter("debug_candidates_before_quality_canonical_negative_value_prefilter")
    counts = pool_filter_counts({
        "day_inventory_membership_keys": 597,
        "debug_candidates_before_quality": 2,
        "debug_candidates_before_quality_stale_or_outside_window": 1,
    })
    assert counts == {"debug_candidates_before_quality_stale_or_outside_window": 1}


def test_dedup_keeps_best_logical_candidate():
    a = C("soccer|a|b|2026-05-25", ev=1)
    b = C("soccer|a|b|2026-05-25", ev=5)
    c = C("soccer|a|b|2026-05-25", point=3.5, ev=2)
    kept, dupes = dedupe_candidates([a, b, c])
    assert len(kept) == 2
    assert b in kept
    assert c in kept
    assert len(dupes) == 1
