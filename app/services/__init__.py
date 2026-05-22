from __future__ import annotations

__all__: list[str] = []

try:
    from app.services import core_line_bookmaker_universe_patch as _core_line_universe
    _core_line_universe.install()
except Exception:
    pass

# Keep controlled-rescue candidates honest before the fallback publisher sees them:
# a market-consensus rescue row must already have non-negative value vs selected odds.
try:
    from app.services import controlled_rescue_consensus_guard_patch as _controlled_rescue_consensus_guard
    _controlled_rescue_consensus_guard.install()
except Exception:
    pass

# Progressive coverage reports should describe only providers that are actually
# active in the current run budget. Optional providers with budget=0 are excluded
# from the active core contract and shown as excluded instead.
try:
    from app.services import progressive_active_core_budget_patch as _progressive_active_core_budget
    _progressive_active_core_budget.install()
except Exception:
    pass

try:
    from app.services import model_input_market_sanity_patch as _model_market_sanity
    _model_market_sanity.install()
except Exception:
    pass
