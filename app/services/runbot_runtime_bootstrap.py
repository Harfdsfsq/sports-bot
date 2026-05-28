from __future__ import annotations

import os
from typing import Callable

INSTALLED: list[str] = []
FAILED: list[str] = []
SKIPPED: list[str] = []


def _truthy(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on", "force"}


def _try_install(name: str, installer: Callable[[], object]) -> None:
    if not _truthy(f"RUNBOT_BOOTSTRAP_{name.upper()}_ENABLED", False):
        SKIPPED.append(name)
        return
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
        "odds_api_io_h2h_parser_runtime_guard",
        lambda: __import__("app.services.odds_api_io_h2h_parser_runtime_guard", fromlist=["install"]).install(),
    )
    _try_install(
        "sportlogic_query_runtime_guard",
        lambda: __import__("app.services.sportlogic_query_runtime_guard", fromlist=["install"]).install(),
    )
    _try_install(
        "targeted_enrichment_runtime_patch",
        lambda: __import__("app.services.targeted_enrichment_runtime_patch", fromlist=["install"]).install(),
    )
    return {"installed": INSTALLED, "failed": FAILED, "skipped": SKIPPED}


if _truthy("RUNBOT_RUNTIME_BOOTSTRAP_AUTOINSTALL", False) and not INSTALLED and not FAILED and not SKIPPED:
    install()
