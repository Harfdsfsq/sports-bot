from __future__ import annotations

"""Service package marker.

Runtime patches are installed explicitly by ``app.services.runtime_startup_chain``
during the main production run.  Importing ``app.services`` must stay side-effect
free: report/fallback helper processes import service modules while reading
artifacts, and automatic installers here can overwrite build-time diagnostics
(for example latest-post-integrity-candidate-rescue.json) after the real run.
"""

__all__: list[str] = []
