from __future__ import annotations

"""Repository startup shim.

Production policy now lives in normal application modules and workflow files.
This file only keeps local helper scripts importable; legacy runtime patch
chains can be enabled explicitly with LEGACY_SITECUSTOMIZE_ENABLED=true for
forensics.
"""

import importlib
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

os.environ.setdefault("PROVIDER_CONTEXT_SOURCES_DO_NOT_CONFIRM_PRICE", "true")


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on", "force"}


def install_legacy_sitecustomize() -> dict[str, str]:
    modules = (
        "app.services.api_runtime_enhancements",
        "app.providers.odds_api_io_startup_compat",
    )
    results: dict[str, str] = {}
    for module_path in modules:
        try:
            module = importlib.import_module(module_path)
            installer = getattr(module, "install", None)
            if callable(installer):
                installer()
            results[module_path] = "ok"
        except Exception as exc:
            results[module_path] = f"{type(exc).__name__}: {exc}"
    return results


if _truthy(os.getenv("LEGACY_SITECUSTOMIZE_ENABLED")):
    install_legacy_sitecustomize()
