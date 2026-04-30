"""Startup hook for scripts executed as `python scripts/*.py`.

For script entrypoints Python imports this file from the scripts directory. It
must delegate to centralized runtime patches and the per-run-only API governor
patch before each workflow script step.
"""

from __future__ import annotations

try:
    import api_max_usage_patch
    import runtime_startup_patches

    api_max_usage_patch.apply_api_max_usage_patch()
    runtime_startup_patches.apply_all()
    runtime_startup_patches.install_import_hook()
except Exception:
    # Startup patches must never break a production run.
    pass
