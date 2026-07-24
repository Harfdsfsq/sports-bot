"""Compatibility import for the stable Focused Alpha runtime contract."""

from app.services.focused_alpha_runtime_contract import POLICY, apply

try:
    from app.services.focused_alpha_accumulation_runtime_patch import install as _install_accumulation

    ACCUMULATION_PATCH = _install_accumulation()
except Exception as exc:
    ACCUMULATION_PATCH = {
        "status": "install_error",
        "error": f"{type(exc).__name__}: {exc}",
        "publication_contract_relaxed": False,
    }

__all__ = ["ACCUMULATION_PATCH", "POLICY", "apply"]
