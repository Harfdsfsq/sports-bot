"""Repository-wide startup hook for Harizon sports-bot.

Python imports this module automatically before app code when the repository
root is on sys.path.  Keep it tiny and delegate all runtime fixes to the
centralized patch module.
"""

from __future__ import annotations

try:
    from scripts.runtime_startup_patches import apply_all, install_import_hook

    apply_all()
    install_import_hook()
except Exception:
    # Startup hooks must never break bot execution.
    pass
