"""Compatibility import for the stable Focused Alpha runtime contract."""

from typing import Any

from app.services.focused_alpha_runtime_contract import POLICY
from app.services.focused_alpha_runtime_contract import apply as _apply_contract

ACCUMULATION_PATCH: dict[str, Any] = {
    "status": "pending",
    "publication_contract_relaxed": False,
}
SETTLEMENT_PAGINATION_PATCH: dict[str, Any] = {
    "status": "pending",
    "publication_contract_relaxed": False,
}
_PATCHES_INSTALLED = False


def _install_runtime_patches() -> None:
    """Install runtime monkey-patches lazily when policy application begins."""

    global ACCUMULATION_PATCH, SETTLEMENT_PAGINATION_PATCH, _PATCHES_INSTALLED
    if _PATCHES_INSTALLED:
        return
    try:
        from app.services.focused_alpha_accumulation_runtime_patch_v2 import (
            install as install_accumulation,
        )

        ACCUMULATION_PATCH = install_accumulation()
    except Exception as exc:
        ACCUMULATION_PATCH = {
            "status": "install_error",
            "error": f"{type(exc).__name__}: {exc}",
            "publication_contract_relaxed": False,
        }
    try:
        from app.services.settlement_sstats_pagination_runtime_patch import (
            install as install_settlement_pagination,
        )

        SETTLEMENT_PAGINATION_PATCH = install_settlement_pagination()
    except Exception as exc:
        SETTLEMENT_PAGINATION_PATCH = {
            "status": "install_error",
            "error": f"{type(exc).__name__}: {exc}",
            "publication_contract_relaxed": False,
        }
    _PATCHES_INSTALLED = True


def apply(*, force: bool = False) -> dict[str, Any]:
    _install_runtime_patches()
    return _apply_contract(force=force)

__all__ = [
    "ACCUMULATION_PATCH",
    "POLICY",
    "SETTLEMENT_PAGINATION_PATCH",
    "apply",
]
