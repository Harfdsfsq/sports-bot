from __future__ import annotations

"""Lightweight bootstrap for the normal GitHub Actions bot run.

Do not import usercustomize here.  usercustomize contains many historical patch
modules and can make a normal run too heavy.  This bootstrap loads only the
runtime pieces needed for the current provider integration and price-safety
work.
"""

from typing import Callable


INSTALLED: list[str] = []
FAILED: list[str] = []


def _try_install(name: str, installer: Callable[[], object]) -> None:
    try:
        installer()
        INSTALLED.append(name)
    except Exception:
        FAILED.append(name)


def install() -> dict[str, list[str]]:
    _try_install(
        "runtime_provider_budget_guard",
        lambda: __import__("app.services.runtime_provider_budget_guard", fromlist=["install"]).install(),
    )
    _try_install(
        "free_context_runtime_enrichment",
        lambda: __import__("app.services.free_context_runtime_enrichment", fromlist=["install"]).install(),
    )
    _try_install(
        "api_matching_quality_runtime_guard",
        lambda: __import__("app.services.api_matching_quality_runtime_guard", fromlist=["install"]).install(),
    )
    _try_install(
        "sportlogic_query_runtime_guard",
        lambda: __import__("app.services.sportlogic_query_runtime_guard", fromlist=["install"]).install(),
    )
    return {"installed": INSTALLED, "failed": FAILED}


if not INSTALLED and not FAILED:
    install()
