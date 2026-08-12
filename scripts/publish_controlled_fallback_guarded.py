from __future__ import annotations

import os
from typing import Any


def _force_runtime_publication_contract() -> None:
    overrides = {
        "PUBLISH_TIER_A_MIN_ODDS_SOURCES": "2", "PUBLISH_TIER_A_MIN_BOOKS": "2", "PUBLISH_TIER_A_MIN_CONTEXT_SOURCES": "2",
        "PUBLISH_TIER_B_MIN_ODDS_SOURCES": "1", "PUBLISH_TIER_B_MIN_BOOKS": "2", "PUBLISH_TIER_B_MIN_CONTEXT_SOURCES": "1",
        "CONTROLLED_FALLBACK_MIN_ODDS_SOURCES": "1", "CONTROLLED_FALLBACK_MIN_CONTEXT_SOURCES": "1", "CONTROLLED_FALLBACK_MIN_CONFIRMATION_SOURCES": "1",
        "CONTROLLED_FALLBACK_TIER_B_MIN_BOOKS": "2", "CONTROLLED_FALLBACK_TIER_B_MIN_CONTEXT_SOURCES": "1", "CONTROLLED_FALLBACK_TIER_B_MIN_CONFIRMATION_SOURCES": "1",
        "CONTROLLED_FALLBACK_REQUIRE_2_ODDS_SOURCES_FOR_TELEGRAM": "false", "CONTROLLED_FALLBACK_REQUIRE_2_CONTEXT_SOURCES_FOR_TELEGRAM": "false",
        "CONTROLLED_FALLBACK_ALLOW_MARKET_IMPLIED_XG_FOR_B_TIER": "true", "CONTROLLED_FALLBACK_BLOCK_PROXY_DEFAULT_XG_ALL_TIERS": "true",
        "CONTROLLED_FALLBACK_B_TIER_REQUIRE_HARD_CONTEXT": "false", "CONTROLLED_FALLBACK_B_TIER_BLOCK_LOW_QUALITY_COMPETITIONS": "true", "CONTROLLED_FALLBACK_TIER_B_WEIGHTED_REQUIRE_XG_HARD_CONFIRMATION": "false",
        "CONTROLLED_FALLBACK_BASE_TIER_B_MIN_PUBLICATION_SCORE": "12.0", "CONTROLLED_FALLBACK_TIER_B_MIN_PUBLICATION_SCORE": "12.0", "CONTROLLED_FALLBACK_TIER_B_MIN_EDGE_PP": "2.3", "CONTROLLED_FALLBACK_TIER_B_MIN_EV_PCT": "4.0", "CONTROLLED_FALLBACK_FINAL_MIN_EDGE_PP": "2.3", "CONTROLLED_FALLBACK_FINAL_MIN_EV_PCT": "4.0",
        "CONTROLLED_FALLBACK_DAILY_MAX_PUBLISHED": "3", "CONTROLLED_FALLBACK_DAILY_MAX_B_TIER": "3",
        "A_TIER_TARGETED_ENRICHMENT_ENABLED": "true", "BZZOIRO_TARGETED_ODDS_CONFIRMATION_ENABLED": "true", "SSTATS_TARGETED_CONTEXT_PROJECTION_ENABLED": "true", "HIGH_VALUE_FAST_RECHECK_ENABLED": "true",
        "BZZOIRO_CONTEXT_MATCH_LIMIT": "300", "BZZOIRO_ODDS_MATCH_LIMIT": "300", "BZZOIRO_PRICE_BACKFILL_TARGET_LIMIT": "220", "SSTATS_CONTEXT_MATCH_LIMIT": "320", "SSTATS_DEEP_CONTEXT_MATCH_LIMIT": "80",
        "DAY_INVENTORY_ENABLE_SPORTLOGIC": "false", "ENABLE_SPORTLOGIC": "false", "SPORTLOGIC_ENABLED": "false", "SPORTLOGIC_CONTROLLED_ODDS_ENABLED": "false", "SPORTLOGIC_PER_RUN_MAX": "0", "SPORTLOGIC_REQUEST_BUDGET_GRANTED": "0", "SPORTLOGIC_MATCH_LIMIT": "0", "SPORTLOGIC_CONTEXT_MATCH_LIMIT": "0", "SPORTLOGIC_ODDS_MATCH_LIMIT": "0", "SPORTLOGIC_DISABLED_ZERO_ROWS_GUARD": "true",
    }
    for k, v in overrides.items(): os.environ[k] = v


def _num(v: Any, d: float = 0.0) -> float:
    try: return float(str(v).replace(",", ".")) if v not in (None, "") else d
    except Exception: return d


def _int(v: Any, d: int = 0) -> int:
    try:
        if isinstance(v, (list, tuple, set, dict)): return len(v)
        return int(float(str(v).replace(",", "."))) if v not in (None, "") else d
    except Exception: return d


def _b_tier_testing_floor(metrics: dict[str, Any]) -> bool:
    books = max(_int(metrics.get("books_count")), _int(metrics.get("bookmaker_count")))
    odds_sources = max(_int(metrics.get("odds_sources_count")), _int(metrics.get("line_sources_count")), _int(metrics.get("sources_count")))
    ctx = max(_int(metrics.get("context_sources_count")), _int(metrics.get("confirmation_sources_count")))
    ev = max(_num(metrics.get("canonical_ev_pct")), _num(metrics.get("ev_pct")))
    edge = max(_num(metrics.get("canonical_edge_pp")), _num(metrics.get("edge_pp")))
    odds = _num(metrics.get("odds"), 0.0)
    return books >= 2 and odds_sources >= 1 and ctx >= 1 and ev >= 4.0 and edge >= 2.3 and 1.70 <= odds <= 2.70


def _install_b_tier_testing_relief(base: Any) -> None:
    old = getattr(base, "tier_reasons", None)
    if not callable(old) or getattr(base, "_b_tier_testing_relief_installed", False): return
    def wrapped(tier: str, candidate: dict[str, Any], metrics: dict[str, Any]) -> list[str]:
        reasons = list(old(tier, candidate, metrics) or [])
        if str(tier or "").strip().upper() != "B" or not _b_tier_testing_floor(metrics): return reasons
        filtered = []
        for reason in reasons:
            r = str(reason)
            if r in {"tier_b_quality_below_min", "tier_b_publication_score_below_min", "tier_b_market_implied_xg_not_hard_confirmation"}: continue
            if r.startswith("tier_b_context_sources_below_min") or r.startswith("tier_b_confirmation_sources_below_min"): continue
            filtered.append(reason)
        return filtered
    base.tier_reasons = wrapped; base._b_tier_testing_relief_installed = True


def _run_step(module_name: str) -> None:
    try:
        module = __import__(module_name, fromlist=["main"]); fn = getattr(module, "main", None)
        if callable(fn): fn()
    except SystemExit: pass
    except Exception: pass


def _apply_focused_alpha_policy() -> None:
    try:
        from app.services.focused_alpha_runtime_policy import apply; apply(force=True)
    except Exception: pass
    _force_runtime_publication_contract()


def _repair_runtime_artifacts_before_fallback() -> None:
    for module_name in ("scripts.bridge_runtime_context_coverage", "scripts.build_day_inventory_coverage_truth", "scripts.harizon_a_tier_coverage_plan", "scripts.harizon_a_tier_targeted_enrichment_queue", "scripts.apply_a_tier_targeted_provider_env", "scripts.project_sstats_context_into_candidates", "scripts.replace_rescue_proxy_placeholder_xg", "scripts.day_inventory_cumulative_coverage"):
        _run_step(module_name)


def _build_focused_alpha_decisions() -> None:
    try:
        from scripts.build_focused_alpha_decisions_v2 import main as build_decisions; build_decisions()
        from app.services.focused_alpha_learning_ledger import update_learning_ledger; update_learning_ledger()
    except Exception: pass


def main() -> int:
    _force_runtime_publication_contract(); _repair_runtime_artifacts_before_fallback(); _apply_focused_alpha_policy()
    try:
        from scripts import apply_controlled_fallback_performance_policy; apply_controlled_fallback_performance_policy.main()
    except Exception: pass
    _force_runtime_publication_contract()
    try:
        import scripts.publish_controlled_fallback_guarded_v18 as v18
        from scripts.harizon_production_quality_layer import install as install_quality_layer
        from scripts.patch_current_price_recheck_value import install as install_current_price_recheck
        from scripts.patch_daily_cap_after_quality import install as install_daily_cap_after_quality
        from scripts.patch_daily_slot_bundle_cap import install as install_daily_slot_bundle_cap
        from scripts.patch_daily_slot_semantic_ledger_count import install as install_semantic_ledger_daily_count
        from scripts.patch_display_line_count_safe import install as install_display_line_count_safe
        from scripts.patch_fallback_current_run_only import install as install_fallback_current_run_only
        from scripts.patch_focused_alpha_candidate_rank import install as install_focused_alpha_rank
        from scripts.patch_publication_safety_contract import install as install_publication_safety_contract
        from scripts.patch_reserved_slot_expiring_candidate import install as install_reserved_slot_expiry_override
        from scripts.patch_same_match_total_conflict_guard import install as install_same_match_total_conflict_guard
        from scripts.patch_semantic_line_movement_alias_relief import install as install_semantic_line_alias_relief
        from scripts.patch_semantic_movement_current_price_guard import install as install_semantic_movement_current_price_guard
        from scripts.patch_tier_a_strict_policy import install as install_tier_a_strict_policy
        install_tier_a_strict_policy(v18.base); install_publication_safety_contract(v18.base); install_semantic_movement_current_price_guard(v18.base); install_current_price_recheck(v18.base); install_semantic_line_alias_relief(v18.base); install_quality_layer(v18.base); _install_b_tier_testing_relief(v18.base)
        install_semantic_ledger_daily_count(v18); install_reserved_slot_expiry_override(v18); install_daily_slot_bundle_cap(v18); install_display_line_count_safe(v18.base); install_same_match_total_conflict_guard(v18.base); install_fallback_current_run_only(v18.base); install_daily_cap_after_quality(v18); install_focused_alpha_rank(v18.base)
    except Exception: pass
    _apply_focused_alpha_policy(); _repair_runtime_artifacts_before_fallback(); _build_focused_alpha_decisions(); _force_runtime_publication_contract()
    from scripts.publish_controlled_fallback_guarded_v20 import main as v20_main
    code = int(v20_main() or 0)
    _build_focused_alpha_decisions(); return code

if __name__ == "__main__": raise SystemExit(main())
