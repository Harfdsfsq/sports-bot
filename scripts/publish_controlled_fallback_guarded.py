from __future__ import annotations


def _apply_focused_alpha_policy() -> None:
    try:
        from app.services.focused_alpha_runtime_policy import apply

        apply(force=True)
    except Exception:
        pass


def _build_focused_alpha_decisions() -> None:
    try:
        from scripts.build_focused_alpha_decisions import main as build_decisions

        build_decisions()
        from app.services.focused_alpha_learning_ledger import update_learning_ledger

        update_learning_ledger()
    except Exception:
        pass


def main() -> int:
    # This helper runs in a separate process from app.cli. Reapply the final
    # Focused Alpha contract here so workflow or legacy fallback defaults cannot
    # restore B-tier publication, proxy quality, a three-pick cap or one-source
    # evidence after the main data-collection process exits.
    _apply_focused_alpha_policy()
    try:
        from scripts import apply_controlled_fallback_performance_policy

        apply_controlled_fallback_performance_policy.main()
    except Exception:
        pass
    try:
        import scripts.publish_controlled_fallback_guarded_v18 as v18
        from scripts.patch_reserved_slot_expiring_candidate import install as install_reserved_slot_expiry_override
        from scripts.patch_daily_slot_bundle_cap import install as install_daily_slot_bundle_cap
        from scripts.patch_daily_slot_semantic_ledger_count import install as install_semantic_ledger_daily_count
        from scripts.patch_daily_cap_after_quality import install as install_daily_cap_after_quality
        from scripts.patch_display_line_count_safe import install as install_display_line_count_safe
        from scripts.patch_same_match_total_conflict_guard import install as install_same_match_total_conflict_guard
        from scripts.patch_fallback_current_run_only import install as install_fallback_current_run_only
        from scripts.patch_tier_a_strict_policy import install as install_tier_a_strict_policy
        from scripts.patch_focused_alpha_candidate_rank import install as install_focused_alpha_rank

        install_tier_a_strict_policy(v18.base)
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

    # Legacy installers above may change environment values. Focused Alpha is the
    # last policy writer before evaluation.
    _apply_focused_alpha_policy()
    _build_focused_alpha_decisions()

    from scripts.publish_controlled_fallback_guarded_v20 import main as v20_main

    code = int(v20_main() or 0)
    # Rebuild after evaluation as well: the final artifact then includes candidates
    # restored by movement lifecycle and all current-run fields without granting
    # the shadow board any right to publish.
    _build_focused_alpha_decisions()
    return code


if __name__ == "__main__":
    raise SystemExit(main())
