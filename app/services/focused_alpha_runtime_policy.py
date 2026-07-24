"""Compatibility import for the stable Focused Alpha runtime contract."""

from app.services.focused_alpha_runtime_contract import POLICY, apply

try:
    from app.services.focused_alpha_accumulation_runtime_patch_v2 import install as _install_accumulation

    ACCUMULATION_PATCH = _install_accumulation()
except Exception as exc:
    ACCUMULATION_PATCH = {
        "status": "install_error",
        "error": f"{type(exc).__name__}: {exc}",
        "publication_contract_relaxed": False,
    }

try:
    from app.services.settlement_sstats_pagination_runtime_patch import install as _install_settlement_pagination

    SETTLEMENT_PAGINATION_PATCH = _install_settlement_pagination()
except Exception as exc:
    SETTLEMENT_PAGINATION_PATCH = {
        "status": "install_error",
        "error": f"{type(exc).__name__}: {exc}",
        "publication_contract_relaxed": False,
    }

__all__ = [
    "ACCUMULATION_PATCH",
    "POLICY",
    "SETTLEMENT_PAGINATION_PATCH",
    "apply",
]
