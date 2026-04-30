"""Startup hook for scripts executed as `python scripts/*.py`.

For script entrypoints Python imports this file from the scripts directory. It
must delegate to centralized runtime patches, the per-run-only API governor,
and controlled-fallback confirmation-source patch before each workflow script
step.
"""

from __future__ import annotations

try:
    import api_max_usage_patch
    import runtime_startup_patches
    import apply_confirmation_source_fallback_patch

    api_max_usage_patch.apply_api_max_usage_patch()
    runtime_startup_patches.apply_all()
    apply_confirmation_source_fallback_patch.main()
    runtime_startup_patches.install_import_hook()
except Exception:
    # Startup patches must never break a production run.
    pass
