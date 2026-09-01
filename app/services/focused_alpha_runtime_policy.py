"""Optional Focused Alpha shadow policy and production rules bootstrap."""
import os
from typing import Any
from app.services.focused_alpha_runtime_contract import POLICY
from app.services.focused_alpha_runtime_contract import apply as _apply_contract

ACCUMULATION_PATCH: dict[str, Any] = {"status": "disabled", "publication_contract_relaxed": False}
SETTLEMENT_PAGINATION_PATCH: dict[str, Any] = {"status": "disabled", "publication_contract_relaxed": False}
_PATCHES_INSTALLED = False

RULES_AB_INVARIANTS = {
    "PUBLICATION_PROFILE": "rules_ab",
    "PUBLISH_ALLOW_B_TIER": "true",
    "PUBLISH_COVERAGE_TIER_MODE": "a_or_b",
    "HARIZON_PUBLICATION_TIER_MODE": "a_or_b",
    "PUBLISH_TIER_B_MIN_BOOKS": "2",
    "PUBLISH_TIER_B_MIN_ODDS_SOURCES": "1",
    "PUBLISH_TIER_B_MIN_CONTEXT_SOURCES": "1",
    "PUBLISH_MIN_ODDS_SOURCES": "1",
    "MIN_SOURCES_PUBLISH": "1",
    "PUBLISH_MIN_CONTEXT_SOURCES": "1",
    "MIN_CONTEXT_SOURCES_PUBLISH": "1",
    "FINAL_ENRICHMENT_ONLY_FOR_VALUE_CANDIDATES": "true",
    "FINAL_ENRICHMENT_FALLBACK_NEAREST_MATCH_LIMIT": "0",
    "NO_BET_QUALITY_SCORE_ENABLED": "false",
    "HARIZON_AUTONOMOUS_ACCUMULATION_MODE": "false",
    "LEGACY_RUNTIME_EXTENSIONS_ENABLED": "false",
}

def _enabled() -> bool:
    return os.getenv("FOCUSED_ALPHA_RUNTIME_POLICY_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}

def _apply_rules_ab_invariants() -> dict[str, object]:
    for key, value in RULES_AB_INVARIANTS.items():
        os.environ[key] = value
    try:
        from app.services.rules_source_integrity import install
        return install()
    except Exception as exc:
        return {"installed": False, "error": f"{type(exc).__name__}: {exc}"}

def _install_runtime_patches() -> None:
    global ACCUMULATION_PATCH, SETTLEMENT_PAGINATION_PATCH, _PATCHES_INSTALLED
    if _PATCHES_INSTALLED or not _enabled():
        return
    try:
        from app.services.focused_alpha_accumulation_runtime_patch_v2 import install
        ACCUMULATION_PATCH = install()
    except Exception as exc:
        ACCUMULATION_PATCH = {"status": "install_error", "error": f"{type(exc).__name__}: {exc}", "publication_contract_relaxed": False}
    try:
        from app.services.settlement_sstats_pagination_runtime_patch import install
        SETTLEMENT_PAGINATION_PATCH = install()
    except Exception as exc:
        SETTLEMENT_PAGINATION_PATCH = {"status": "install_error", "error": f"{type(exc).__name__}: {exc}", "publication_contract_relaxed": False}
    _PATCHES_INSTALLED = True

def apply(*, force: bool = False) -> dict[str, Any]:
    if not _enabled():
        integrity = _apply_rules_ab_invariants()
        return {"enabled": False, "status": "disabled_for_rules_ab", "publication_contract_relaxed": False, "rules_invariants_applied": True, "source_integrity": integrity}
    _install_runtime_patches()
    return _apply_contract(force=force)

__all__ = ["ACCUMULATION_PATCH", "POLICY", "RULES_AB_INVARIANTS", "SETTLEMENT_PAGINATION_PATCH", "apply"]
