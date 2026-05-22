from __future__ import annotations

__all__: list[str] = []

# Base candidate-source relaxations: allow Tier-B discovery with 1 line source
# while keeping final Telegram publication strict.
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

# Re-install the final CandidateFactory wrapper chain in a deterministic order:
# core line universe -> market sanity -> canonical value filter.  Without this
# late chain, zero-candidate runs can happen even when odds/context coverage is
# present because an older wrapper remains outermost.
try:
    from app.services import final_candidate_runtime_chain as _final_candidate_runtime_chain
    _final_candidate_runtime_chain.install()
except Exception:
    pass

# If hard market-integrity leaves the raw pool empty, rebuild controlled reserve
# candidates so the quality/fallback layer can evaluate them.  This still does
# not publish anything directly; final EV/xG/context guards stay in Telegram
# fallback.
try:
    from app.services import post_integrity_candidate_rescue as _post_integrity_candidate_rescue
    _post_integrity_candidate_rescue.install()
except Exception:
    pass

try:
    from app.services import controlled_rescue_consensus_guard_patch as _controlled_rescue_consensus_guard
    _controlled_rescue_consensus_guard.install()
except Exception:
    pass

try:
    from app.services import progressive_active_core_budget_patch as _progressive_active_core_budget
    _progressive_active_core_budget.install()
except Exception:
    pass
