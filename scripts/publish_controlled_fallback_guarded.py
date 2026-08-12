from __future__ import annotations

import os


def _force_runtime_publication_contract() -> None:
    """Final contract for the separate guarded-fallback process.

    The main run and workflow write a lot of legacy env values.  This script runs
    in a new Python process, so we enforce the intended testable publication
    contract here as the last writer before candidate evaluation:
      * A-tier remains strict: 2 odds / 2 books / 2 context.
      * B-tier is testable but still safe: 1 odds / 2 books / 1 context.
      * price-integrity, value, quality, xG sanity, movement and dedupe guards
        remain enabled.
      * SportLogic stays disabled while the endpoint returns zero rows.
    """
    overrides = {
        "PUBLISH_TIER_A_MIN_ODDS_SOURCES": "2",
        "PUBLISH_TIER_A_MIN_BOOKS": "2",
        "PUBLISH_TIER_A_MIN_CONTEXT_SOURCES": "2",
        "PUBLISH_TIER_B_MIN_ODDS_SOURCES": "1",
        "PUBLISH_TIER_B_MIN_BOOKS": "2",
        "PUBLISH_TIER_B_MIN_CONTEXT_SOURCES": "1",
        "CONTROLLED_FALLBACK_MIN_ODDS_SOURCES": "1",
        "CONTROLLED_FALLBACK_MIN_CONTEXT_SOURCES": "1",
        "CONTROLLED_FALLBACK_MIN_CONFIRMATION_SOURCES": "1",
        "CONTROLLED_FALLBACK_TIER_B_MIN_BOOKS": "2",
        "CONTROLLED_FALLBACK_TIER_B_MIN_CONTEXT_SOURCES": "1",
        "CONTROLLED_FALLBACK_TIER_B_MIN_CONFIRMATION_SOURCES": "1",
        "CONTROLLED_FALLBACK_REQUIRE_2_ODDS_SOURCES_FOR_TELEGRAM": "false",
        "CONTROLLED_FALLBACK_REQUIRE_2_CONTEXT_SOURCES_FOR_TELEGRAM": "false",
        "CONTROLLED_FALLBACK_ALLOW_MARKET_IMPLIED_XG_FOR_B_TIER": "true",
        "CONTROLLED_FALLBACK_BLOCK_PROXY_DEFAULT_XG_ALL_TIERS": "true",
        "CONTROLLED_FALLBACK_B_TIER_REQUIRE_HARD_CONTEXT": "false",
        "CONTROLLED_FALLBACK_B_TIER_BLOCK_LOW_QUALITY_COMPETITIONS": "true",
        "CONTROLLED_FALLBACK_TIER_B_WEIGHTED_REQUIRE_XG_HARD_CONFIRMATION": "false",
        "ENABLE_SPORTLOGIC": "false",
        "SPORTLOGIC_ENABLED": "false",
        "SPORTLOGIC_CONTROLLED_ODDS_ENABLED": "false",
        "SPORTLOGIC_BROAD_FALLBACK_ENABLED": "false",
        "SPORTLOGIC_ODDS_DISCOVERY_FALLBACK_ENABLED": "false",
        "SPORTLOGIC_PER_RUN_MAX": "0",
        "SPORTLOGIC_MAX_REQUESTS_PER_RUN": "0",
        "SPORTLOGIC_MAX_HTTP_REQUESTS_PER_RUN": "0",
        "SPORTLOGIC_REQUESTS_MAX_PER_RUN": "0",
        "SPORTLOGIC_REQUEST_BUDGET_GRANTED": "0",
        "SPORTLOGIC_MATCH_LIMIT": "0",
        "SPORTLOGIC_CONTEXT_MATCH_LIMIT": "0",
        "SPORTLOGIC_ODDS_MATCH_LIMIT": "0",
        "SPORTLOGIC_DISABLED_ZERO_ROWS_GUARD": "true",
    }
    for key, value in overrides.items():
        os.environ[key] = value


def _apply_focused_alpha_policy() -> None:
    try:
        from app.services.focused_alpha_runtime_policy import apply

        apply(force=True)
    except Exception:
        pass
    _force_runtime_publication_contract()


def _repair_runtime_artifacts_before_fallback() -> None:
    """Make fallback evaluate repaired current-run evidence, not stale raw rows."""
    steps = (
        ("scripts.bridge_runtime_context_coverage", "main"),
        ("scripts.build_day_inventory_coverage_truth", "main"),
        ("scripts.replace_rescue_proxy_placeholder_xg", "main"),
        ("scripts.day_inventory_cumulative_coverage", "main"),
    )
    for module_name, function_name in steps:
        try:
            module = __import__(module_name, fromlist=[function_name])
            fn = getattr(module, function_name, None)
            if callable(fn):
                fn()
        except SystemExit:
            pass
        except Exception:
            pass


def _build_focused_alpha_decisions() -> None:
    try:
        from scripts.build_focused_alpha_decisions_v2 import main as build_decisions

        build_decisions()
        from app.services.focused_alpha_learning_ledger import update_learning_ledger

        update_learning_ledger()
    except Exception:
        pass


def main() -> int:
    _force_runtime_publication_contract()
    _repair_runtime_artifacts_before_fallback()
    _apply_focused_alpha_policy()
    try:
        from scripts import apply_controlled_fallback_performance_policy

        apply_controlled_fallback_performance_policy.main()
    except Exception:
        pass
    _force_runtime_publication_contract()
    try:
        import scripts.publish_controlled_fallback_guarded_v18 as v18
        from scripts.patch_daily_cap_after_quality import install as install_daily_cap_after_quality
        from scripts.patch_daily_slot_bundle_cap import install as install_daily_slot_bundle_cap
        from scripts.patch_daily_slot_semantic_ledger_count import install as install_semantic_ledger_daily_count
        from scripts.patch_display_line_count_safe import install as install_display_line_count_safe
        from scripts.patch_fallback_current_run_only import install as install_fallback_current_run_only
        from scripts.patch_focused_alpha_candidate_rank import install as install_focused_alpha_rank
        from scripts.patch_publication_safety_contract import install as install_publication_safety_contract
        from scripts.patch_reserved_slot_expiring_candidate import install as install_reserved_slot_expiry_override
        from scripts.patch_same_match_total_conflict_guard import install as install_same_match_total_conflict_guard
        from scripts.patch_tier_a_strict_policy import install as install_tier_a_strict_policy

        install_tier_a_strict_policy(v18.base)
        install_publication_safety_contract(v18.base)
        install_semantic_ledger_daily_count(v18)
        install_reserved_slot_expiry_override(v18)
        install_daily_slot_bundle_cap(v18)
        install_display_line_count_safe(v18.base)
        install_same_match_total_conflict_guard(v18.base)
        install_fallback_current_run_only(v18.base)
        install_daily_cap_after_quality(v18)
        install_focused_alpha_rank(v18.base)
    except Exception:
        pass

    _apply_focused_alpha_policy()
    _repair_runtime_artifacts_before_fallback()
    _build_focused_alpha_decisions()
    _force_runtime_publication_contract()

    from scripts.publish_controlled_fallback_guarded_v20 import main as v20_main

    code = int(v20_main() or 0)
    _build_focused_alpha_decisions()
    return code


if __name__ == "__main__":
    raise SystemExit(main())
