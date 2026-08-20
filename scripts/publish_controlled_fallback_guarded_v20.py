from __future__ import annotations

import os

import scripts.publish_controlled_fallback_guarded_v19 as v19


def _apply_rules_runtime_env() -> None:
    # NOTE: this layer runs last, so anything set here wins over
    # scripts/publish_controlled_fallback_guarded.py. Keep the two in sync.
    defaults = {
        "PUBLISH_TIER_B_MIN_CONTEXT_SOURCES": "1",
        "CONTROLLED_FALLBACK_TIER_B_MIN_CONTEXT_SOURCES": "1",
        "CONTROLLED_FALLBACK_TIER_B_MIN_CONFIRMATION_SOURCES": "1",
        "CONTROLLED_FALLBACK_MIN_CONTEXT_SOURCES": "1",
        "CONTROLLED_FALLBACK_MIN_CONFIRMATION_SOURCES": "1",
        "CONTROLLED_FALLBACK_PUBLISH_WINDOW_HOURS": "24",
        "PUBLISH_WINDOW_HOURS": "24",
        "CONTROLLED_FALLBACK_ALLOW_MARKET_IMPLIED_XG_FOR_B_TIER": "true",
        # One context source is only acceptable when that context is a real
        # provider context (sstats_form/sstats_xg/bzzoiro_stats/odds_comparison).
        "CONTROLLED_FALLBACK_B_TIER_REQUIRE_HARD_CONTEXT": "true",
        "CONTROLLED_FALLBACK_ALLOW_CURRENT_BOOK_SUBSTITUTION": "true",
        "CONTROLLED_FALLBACK_CURRENT_PRICE_ABS_TOLERANCE": "0.05",
        "CONTROLLED_FALLBACK_CURRENT_PRICE_PCT_TOLERANCE": "2.5",
        "CONTROLLED_FALLBACK_REQUIRE_FRESH_SELECTED_PRICE": "false",
        "CONTROLLED_FALLBACK_ALLOW_VALUE_ALIVE_HIGH_DRIFT": "true",
        "CONTROLLED_FALLBACK_CURRENT_RECHECK_MIN_EV_PCT": "3.0",
        "CONTROLLED_FALLBACK_CURRENT_RECHECK_MIN_EDGE_PP": "1.5",
        # B-tier value bar. Must equal _b_tier_testing_floor in
        # scripts/publish_controlled_fallback_guarded.py: the relief cannot strip
        # tier_b_canonical_edge_below_min / _ev_below_min, so a higher bar here
        # would silently veto every candidate the relief accepted.
        "CONTROLLED_FALLBACK_TIER_B_MIN_EDGE_PP": "1.0",
        "CONTROLLED_FALLBACK_TIER_B_MIN_EV_PCT": "2.0",
        "HARIZON_B_RELIEF_MIN_ODDS": "1.70",
        "HARIZON_B_RELIEF_MAX_ODDS": "3.20",
        "HARIZON_B_RELIEF_MIN_EV_PCT": "2.0",
        "HARIZON_B_RELIEF_MIN_EDGE_PP": "1.0",
        # The tier-B contract and the relief floor both demand two bookmakers, so
        # a single-book promotion can never be published; it only consumes a
        # promotion slot. Keep the promotion bar equal to the publication bar.
        "PROMOTE_B_COVER_MIN_BOOKS": "2",
        # A-cover promotion runs in-process below; give it the same window as the
        # publisher instead of its own 2h fallback default.
        "PROMOTE_A_COVER_WINDOW_HOURS": "24",
        "PROMOTE_A_COVER_PREFER_STRICT_EVIDENCE_INVENTORY": "true",
        "PROMOTE_A_COVER_IN_BAND_PRICE_SELECTION": "true",
        "PROMOTE_A_COVER_VALUE_CANDIDATE_LIMIT": "18",
    }
    for key, value in defaults.items():
        os.environ[key] = value


def main() -> int:
    _apply_rules_runtime_env()
    v19.run_preflight()
    for step in ('scripts.build_context_source_index','scripts.restore_awaiting_movement_candidates','scripts.promote_a_cover_value_candidates','scripts.apply_fallback_price_floor'):
        try:
            module = __import__(step, fromlist=['main']); fn = getattr(module, 'main', None)
            if callable(fn): fn()
        except Exception:
            pass
    import scripts.publish_controlled_fallback_guarded_v18 as v18
    from scripts.patch_controlled_fallback_duplicate_matching import install as install_duplicate_matcher
    from scripts.patch_a_cover_evidence_quality import install as install_a_cover_evidence_quality
    from scripts.patch_xg_sanity_probability_support import install as install_xg_probability_support
    from scripts.patch_reference_price_guard import install as install_reference_price_guard
    from scripts.patch_display_line_count_safe import install as install_display_line_count_safe
    from scripts.patch_same_match_total_conflict_guard import install as install_same_match_total_conflict_guard
    from scripts.patch_current_bankroll_source import install as install_current_bankroll_source
    from scripts.patch_market_implied_xg_backfill import install as install_market_implied_xg_backfill
    from scripts.patch_proxy_default_xg_guard import install as install_proxy_default_xg_guard
    from scripts.patch_publication_safety_contract import install as install_publication_safety_contract
    from scripts.patch_controlled_fallback_confirmation_bridge import install as install_confirmation_bridge
    from scripts.patch_current_price_recheck_value import install as install_current_price_recheck_value
    from scripts.patch_semantic_movement_current_price_guard import install as install_semantic_movement_current_price_guard

    install_duplicate_matcher(v18)
    install_a_cover_evidence_quality(v18.base)
    install_xg_probability_support(v18.base)
    install_confirmation_bridge(v18.base)
    # Must stay immediately before the proxy xG guard.
    install_market_implied_xg_backfill(v18.base)
    install_proxy_default_xg_guard(v18.base)
    install_publication_safety_contract(v18.base)
    install_reference_price_guard(v18.base)
    install_display_line_count_safe(v18.base)
    install_same_match_total_conflict_guard(v18.base)
    install_current_bankroll_source(v18.base)
    install_semantic_movement_current_price_guard(v18.base)
    install_current_price_recheck_value(v18.base)
    code = int(v18.main() or 0)
    for step in ('scripts.sync_run_report_ledger_export','scripts.sync_publication_ledger','scripts.sync_controlled_fallback_selected_to_ledger','scripts.sync_run_report_ledger_export','scripts.build_two_plus_coverage_report'):
        try:
            module = __import__(step, fromlist=['main']); fn = getattr(module, 'main', None)
            if callable(fn): fn()
        except Exception:
            pass
    return code


if __name__ == "__main__":
    raise SystemExit(main())
