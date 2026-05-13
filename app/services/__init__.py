from __future__ import annotations

__all__: list[str] = []

try:
    from app.services import core_line_bookmaker_universe_patch as _core_line_universe
    _core_line_universe.install()
except Exception:
    pass

try:
    from app.services import model_input_market_sanity_patch as _model_market_sanity
    _model_market_sanity.install()
except Exception:
    pass
