from __future__ import annotations

import os
from typing import Any


def _force_runtime_publication_contract() -> None:
    """Final contract for the separate guarded-fallback process.

    The main run and workflow write a lot of legacy env values. This script runs
    in a new Python process, so we enforce the intended testable publication
    contract here as the last writer before candidate evaluation:
      * A-tier remains strict: 2 odds / 2 books / 2 context.
      * B-tier is testable but still safe: 1 odds / 2 books / 1 context.
      * price-integrity, value, movement, dedupe and market-family guards remain.
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
        "CONTROLLED_FALLBACK_BASE_TIER_B_MIN_PUBLICATION_SCORE": "12.0",
        "CONTROLLED_FALLBACK_TIER_B_MIN_PUBLICATION_SCORE": "12.0",
        "CONTROLLED_FALLBACK_TIER_B_MIN_EDGE_PP": "2.3",
        "CONTROLLED_FALLBACK_TIER_B_MIN_EV_PCT": "4.0",
        "CONTROLLED_FALLBACK_FINAL_MIN_EDGE_PP": "2.3",
        "CONTROLLED_FALLBACK_FINAL_MIN_EV_PCT": "4.0",
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


def _num(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(str(value).replace(",", "."))
    except Exception:
        return default


def _int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ""):
            return default
        if isinstance(value, (list, tuple, set, dict)):
            return len(value)
        return int(float(str(value).replace(",", ".")))
    except Exception:
        return default


def _b_tier_testing_floor(metrics: dict[str, Any]) -> bool:
    """Whether a B-tier candidate is good enough for test publication.

    This does not bypass movement/price/current-line/dedupe/family guards. It only
    prevents old proxy-quality/publication-score floors from rejecting otherwise
    testable B-tier value candidates whose q is raw_missing/0 because quality data
    was not produced for the reserve path.
    """
    books = max(_int(metrics.get("books_count")), _int(metrics.get("bookmaker_count")))
    odds_sources = max(_int(metrics.get("odds_sources_count")), _int(metrics.get("line_sources_count")), _int(metrics.get("sources_count")))
    ctx = max(_int(metrics.get("context_sources_count")), _int(metrics.get("confirmation_sources_count")))
    ev = max(_num(metrics.get("canonical_ev_pct")), _num(metrics.get("ev_pct")))
    edge = max(_num(metrics.get("canonical_edge_pp")), _num(metrics.get("edge_pp")))
    odds = _num(metrics.get("odds"), 0.0)
    return books >= 2 and odds_sources >= 1 and ctx >= 1 and ev >= 4.0 and edge >= 2.3 and 1.70 <= odds <= 2.70


def _install_b_tier_testing_relief(base: Any) -> None:
    old = getattr(base, "tier_reasons", None)
    if not callable(old) or getattr(base, "_b_tier_testing_relief_installed", False):
        return

    def wrapped(tier: str, candidate: dict[str, Any], metrics: dict[str, Any]) -> list[str]:
        reasons = list(old(tier, candidate, metrics) or [])
        if str(tier or "").strip().upper() != "B":
            return reasons
        if not _b_tier_testing_floor(metrics):
            return reasons
        removable_exact = {
            "tier_b_quality_below_min",
            "tier_b_publication_score_below_min",
            "tier_b_market_implied_xg_not_hard_confirmation",
        }
        filtered: list[str] = []
        for reason in reasons:
            r = str(reason)
            if r in removable_exact:
                continue
            # The actual B contract is 1 context; strip legacy 2-context remnants.
            if r.startswith("tier_b_context_sources_below_min") or r.startswith("tier_b_confirmation_sources_below_min"):
                continue
            filtered.append(reason)
        return filtered

    base.tier_reasons = wrapped
    base._b_tier_testing_relief_installed = True


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
        _install_b_tier_testing_relief(v18.base)
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
