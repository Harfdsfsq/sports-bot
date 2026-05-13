from __future__ import annotations

__all__: list[str] = []

try:
    from app.services import core_line_bookmaker_universe_patch as _core_line_universe
    _core_line_universe.install()
except Exception:
    pass
