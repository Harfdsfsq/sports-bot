from __future__ import annotations

"""Legacy compatibility shim.

Provider budgets are now applied only by scripts/apply_provider_request_budget.py
from config/provider_runtime_policy.json. This module is kept so older workflow
steps can call it safely without rewriting runtime files.
"""


def apply_api_max_usage_patch() -> None:
    return None


if __name__ == "__main__":
    apply_api_max_usage_patch()
