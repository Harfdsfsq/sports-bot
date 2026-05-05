from __future__ import annotations

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
        "bookmaker_universe_runtime_guard",
        lambda: __import__("app.services.bookmaker_universe_runtime_guard", fromlist=["install"]).install(),
    )
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
        "odds_api_io_runtime_compat",
        lambda: __import__("app.services.odds_api_io_runtime_compat", fromlist=["install"]).install(),
    )
    _try_install(
        "sportlogic_query_runtime_guard",
        lambda: __import__("app.services.sportlogic_query_runtime_guard", fromlist=["install"]).install(),
    )
    return {"installed": INSTALLED, "failed": FAILED}


if not INSTALLED and not FAILED:
    install()
