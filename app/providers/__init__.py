__all__ = []

# ``usercustomize.py`` is not guaranteed to load in GitHub Actions when the
# user-site directory is disabled.  Provider package import is part of every
# production run, so install the flat autonomous persistence redirect here
# before the autonomous candidate wrappers are activated by runtime preflight.
try:
    from app.services import autonomous_accumulation_persistence as _autonomous_persistence
    _autonomous_persistence.install()
except Exception:
    pass

try:
    from app.providers import bzzoiro_v2_date_window_patch as _bzzoiro_v2_date_window_patch
    _bzzoiro_v2_date_window_patch.install()
except Exception:
    pass

try:
    from app.providers import bzzoiro_v2_odds_comparison_patch as _bzzoiro_v2_odds_comparison_patch
    _bzzoiro_v2_odds_comparison_patch.install()
except Exception:
    pass

# Must be last: it wraps the fully patched v2 provider, including the odds
# comparison extension, so every detail request shares one request/time budget.
try:
    from app.services import bzzoiro_runtime_budget_patch as _bzzoiro_runtime_budget_patch
    _bzzoiro_runtime_budget_patch.install()
except Exception:
    pass
