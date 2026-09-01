"""Optional Focused Alpha shadow policy.

Production uses the rules-compliant A/B contract. Focused Alpha is installed
only when explicitly enabled, so importing the CLI cannot silently replace the
publication policy.
"""
import os
from typing import Any
from app.services.focused_alpha_runtime_contract import POLICY
from app.services.focused_alpha_runtime_contract import apply as _apply_contract

ACCUMULATION_PATCH: dict[str, Any] = {"status": "disabled", "publication_contract_relaxed": False}
SETTLEMENT_PAGINATION_PATCH: dict[str, Any] = {"status": "disabled", "publication_contract_relaxed": False}
_PATCHES_INSTALLED = False

def _enabled() -> bool:
    return os.getenv("FOCUSED_ALPHA_RUNTIME_POLICY_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}

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
        return {"enabled": False, "status": "disabled_for_rules_ab", "publication_contract_relaxed": False}
    _install_runtime_patches()
    return _apply_contract(force=force)

__all__ = ["ACCUMULATION_PATCH", "POLICY", "SETTLEMENT_PAGINATION_PATCH", "apply"]
