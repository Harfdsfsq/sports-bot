from __future__ import annotations

import os

import scripts.publish_controlled_fallback_guarded_v19 as v19


def _apply_rules_runtime_env() -> None:
    """Keep fallback aligned with RULES.txt without disabling price/movement guards.

    The fallback process is a separate Python entrypoint, so it does not always
    inherit the main runner's runtime_startup_chain overrides.  For B-tier, a
    missing exact selected quote should not be a hard terminal blocker when a
    comparable current quote exists; however current-price EV/edge, line
    movement, source confirmation and xG guards still remain active.
    """
    defaults = {
        "PUBLISH_TIER_B_MIN_CONTEXT_SOURCES": "1",
        "CONTROLLED_FALLBACK_TIER_B_MIN_CONTEXT_SOURCES": "1",
        "CONTROLLED_FALLBACK_TIER_B_MIN_CONFIRMATION_SOURCES": "1",
        "CONTROLLED_FALLBACK_MIN_CONTEXT_SOURCES": "1",
        "CONTROLLED_FALLBACK_MIN_CONFIRMATION_SOURCES": "1",
        "CONTROLLED_FALLBACK_PUBLISH_WINDOW_HOURS": "24",
        "PUBLISH_WINDOW_HOURS": "24",
        "CONTROLLED_FALLBACK_ALLOW_MARKET_IMPLIED_XG_FOR_B_TIER": "true",
        "CONTROLLED_FALLBACK_B_TIER_REQUIRE_HARD_CONTEXT": "false",
        "CONTROLLED_FALLBACK_ALLOW_CURRENT_BOOK_SUBSTITUTION": "true",
        "CONTROLLED_FALLBACK_CURRENT_PRICE_ABS_TOLERANCE": "0.05",
        "CONTROLLED_FALLBACK_CURRENT_PRICE_PCT_TOLERANCE": "2.5",
        "CONTROLLED_FALLBACK_REQUIRE_FRESH_SELECTED_PRICE": "false",
        "CONTROLLED_FALLBACK_ALLOW_VALUE_ALIVE_HIGH_DRIFT": "true",
        "CONTROLLED_FALLBACK_CURRENT_RECHECK_MIN_EV_PCT": "3.0",
        "CONTROLLED_FALLBACK_CURRENT_RECHECK_MIN_EDGE_PP": "1.5",
    }
    for key, value in defaults.items():
        os.environ[key] = value


def main() -> int:
    _apply_rules_runtime_env()
    v19.run_preflight()
    try:
        from scripts.restore_awaiting_movement_candidates import main as restore_awaiting
        restore_awaiting()
    except Exception:
        pass
    try:
        from scripts.apply_fallback_price_floor import main as apply_price_floor
        apply_price_floor()
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
    from scripts.patch_proxy_default_xg_guard import install as install_proxy_default_xg_guard
    from scripts.patch_publication_safety_contract import install as install_publication_safety_contract
    from scripts.patch_current_price_recheck_value import install as install_current_price_recheck_value
    from scripts.patch_semantic_movement_current_price_guard import (
        install as install_semantic_movement_current_price_guard,
    )

    install_duplicate_matcher(v18)
    install_a_cover_evidence_quality(v18.base)
    install_xg_probability_support(v18.base)
    install_proxy_default_xg_guard(v18.base)
    install_publication_safety_contract(v18.base)
    install_reference_price_guard(v18.base)
    install_display_line_count_safe(v18.base)
    install_same_match_total_conflict_guard(v18.base)
    install_current_bankroll_source(v18.base)
    # Order matters: semantic guard can emit selected/current price reasons;
    # current-price patch must run after it to normalize positive EV/edge and
    # avoid false `value lost:+EV/+edge` blockers in the v20 path.
    install_semantic_movement_current_price_guard(v18.base)
    install_current_price_recheck_value(v18.base)
    code = int(v18.main() or 0)
    try:
        from scripts.sync_run_report_ledger_export import main as sync_run_ledger
        sync_run_ledger()
    except Exception:
        pass
    try:
        from scripts.sync_publication_ledger import main as sync_publication_ledger
        sync_publication_ledger()
    except Exception:
        pass
    try:
        from scripts.sync_controlled_fallback_selected_to_ledger import main as sync_selected_fallback
        sync_selected_fallback()
    except Exception:
        pass
    try:
        from scripts.sync_run_report_ledger_export import main as sync_run_ledger
        sync_run_ledger()
    except Exception:
        pass
    try:
        from scripts.build_two_plus_coverage_report import main as two_plus_report
        two_plus_report()
    except Exception:
        pass
    return code


if __name__ == "__main__":
    raise SystemExit(main())
