from __future__ import annotations

"""Optional user-level startup hook for legacy runtime extensions."""

import os
import sys
from pathlib import Path

from sitecustomize import *  # noqa: F401,F403


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
