__all__ = []

# ``usercustomize.py`` is not guaranteed to load in GitHub Actions when the
# user-site directory is disabled. Provider package import is part of every
# production run, so install the flat autonomous persistence redirect here
# before the autonomous candidate wrappers are activated by runtime preflight.
try:
    from app.services import (
        autonomous_accumulation_persistence as _autonomous_persistence,
    )

    _autonomous_persistence.install()
except Exception:
    pass

# odds-api.io can return roughly one thousand events. Install the indexed
# prefilter before the provider runs so exact/loose matches keep their original
# scorer while expensive fuzzy scoring only sees a small kickoff/token shortlist.
try:
    from app.providers import (
        odds_api_io_fast_match_patch as _odds_api_io_fast_match_patch,
    )

    _odds_api_io_fast_match_patch.install()
except Exception:
    pass

try:
    from app.providers import (
        bzzoiro_v2_date_window_patch as _bzzoiro_v2_date_window_patch,
    )

    _bzzoiro_v2_date_window_patch.install()
except Exception:
    pass

try:
    from app.providers import (
        bzzoiro_v2_odds_comparison_patch as _bzzoiro_v2_odds_comparison_patch,
    )

    _bzzoiro_v2_odds_comparison_patch.install()
except Exception:
    pass

# Wrap the fully patched v2 provider so every useful detail request shares one
# process-level request/time budget.
try:
    from app.services import (
        bzzoiro_runtime_budget_patch as _bzzoiro_runtime_budget_patch,
    )

    _bzzoiro_runtime_budget_patch.install()
except Exception:
    pass

# Legacy source-matrix enrichers can still call metadata/prediction directly.
# Install this last so those calls are rejected before they consume the shared
# odds/stats request budget.
try:
    from app.services import (
        bzzoiro_disabled_endpoint_guard as _bzzoiro_disabled_endpoint_guard,
    )

    _bzzoiro_disabled_endpoint_guard.install()
except Exception:
    pass
