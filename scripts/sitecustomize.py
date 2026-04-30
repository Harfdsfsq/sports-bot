"""Startup hook for scripts executed as `python scripts/*.py`.

For script entrypoints Python imports this file from the scripts directory.  It
must delegate to the centralized runtime patcher so API/provider fixes also
apply to all workflow script steps.
"""

from __future__ import annotations

try:
    import runtime_startup_patches

    runtime_startup_patches.apply_all()
    runtime_startup_patches.install_import_hook()
except Exception:
    # Startup patches must never break a production run.
    pass
