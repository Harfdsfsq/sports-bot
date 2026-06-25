from __future__ import annotations

"""Optional user-level startup hook for legacy runtime extensions."""

import os
import sys
from pathlib import Path

from sitecustomize import *  # noqa: F401,F403

A_TIER_ONLY_ENV = {
    "HARIZON_PUBLICATION_TIER_MODE": "a_only",
    "HARIZON_A_TIER_ONLY": "true",
    "PUBLISH_ALLOW_B_TIER": "false",
    "CONTROLLED_FALLBACK_TELEGRAM_ALLOW_TIER_B": "false",
    "CONTROLLED_FALLBACK_ALLOW_B_TIER": "false",
    "CONTROLLED_FALLBACK_TIER_B_ENABLED": "false",
    "CONTROLLED_FALLBACK_DAILY_MAX_B_TIER": "0",
    "PROMOTE_B_COVER_VALUE_CANDIDATES_ENABLED": "false",
    "PROMOTE_B_COVER_AFTER_A_PROMOTION_ENABLED": "false",
    "PROMOTE_A_COVER_PRUNE_RESCUE_TO_PUBLISH_WINDOW": "true",
    "PUBLISH_TIER_A_MIN_ODDS_SOURCES": "2",
    "PUBLISH_TIER_A_MIN_BOOKS": "2",
    "PUBLISH_TIER_A_MIN_CONTEXT_SOURCES": "2",
    "CONTROLLED_FALLBACK_TIER_A_REQUIRE_2_ODDS_SOURCES": "true",
    "CONTROLLED_FALLBACK_TIER_A_MIN_ODDS_SOURCES": "2",
    "CONTROLLED_FALLBACK_TIER_A_MIN_BOOKS": "2",
    "CONTROLLED_FALLBACK_TIER_A_MIN_BOOKMAKERS": "2",
    "CONTROLLED_FALLBACK_TIER_A_MIN_CONTEXT_SOURCES": "2",
    "CONTROLLED_FALLBACK_TIER_A_MIN_CONFIRMATION_SOURCES": "2",
    "PUBLISH_TIER_B_MIN_ODDS_SOURCES": "999",
    "PUBLISH_TIER_B_MIN_BOOKS": "999",
    "PUBLISH_TIER_B_MIN_CONTEXT_SOURCES": "999",
    "CONTROLLED_FALLBACK_TIER_B_MIN_ODDS_SOURCES": "999",
    "CONTROLLED_FALLBACK_TIER_B_MIN_BOOKS": "999",
    "CONTROLLED_FALLBACK_TIER_B_MIN_BOOKMAKERS": "999",
    "CONTROLLED_FALLBACK_TIER_B_MIN_CONTEXT_SOURCES": "999",
    "CONTROLLED_FALLBACK_TIER_B_MIN_CONFIRMATION_SOURCES": "999",
}

os.environ.update(A_TIER_ONLY_ENV)


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on", "force"}


def _is_helper_process() -> bool:
    name = Path(str(sys.argv[0] or "")).name
    return (
        str(sys.argv[0] or "").strip() == "-"
        or name.startswith("publish_controlled_fallback")
        or os.getenv("HARIZON_SKIP_USERCUSTOMIZE_INSTALLERS") == "1"
    )


def install_legacy_usercustomize() -> dict[str, str]:
    try:
        from app.services import runtime_startup_chain

        result = runtime_startup_chain.install_all()
        return {"app.services.runtime_startup_chain": str(result)}
    except Exception as exc:
        return {"app.services.runtime_startup_chain": f"{type(exc).__name__}: {exc}"}


if _truthy(os.getenv("LEGACY_RUNTIME_EXTENSIONS_ENABLED")) and not _is_helper_process():
    install_legacy_usercustomize()
