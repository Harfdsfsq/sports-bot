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
# Install this after the request budget so those calls are rejected before they
# consume the shared odds/stats budget.
try:
    from app.services import (
        bzzoiro_disabled_endpoint_guard as _bzzoiro_disabled_endpoint_guard,
    )

    _bzzoiro_disabled_endpoint_guard.install()
except Exception:
    pass

# A request-claim wall clock is not a true coroutine deadline: requests already
# in flight can overshoot it. Install the outer deadline last so the whole v2
# detail layer returns control to the prediction runner within a bounded time.
try:
    from app.services import (
        bzzoiro_runtime_deadline_patch as _bzzoiro_runtime_deadline_patch,
    )

    _bzzoiro_runtime_deadline_patch.install()
except Exception:
    pass

# Production disables the legacy startup chain. Activate strict coverage repair
# through the provider package, which is imported on every real run, and let it
# reassert itself after native preflight/source-matrix installers.
try:
    import os as _os

    # A matched Bzzoiro price row is valid independent line evidence, but it is
    # not a second sporting context by itself. Only prediction/model payloads
    # may satisfy the Bzzoiro context slot.
    _os.environ["BZZOIRO_ODDS_MATCH_COUNTS_AS_EVENT_CONTEXT"] = "false"

    from app.services import (
        strict_coverage_native_activation as _strict_coverage_native_activation,
    )

    _strict_coverage_native_activation.install()
except Exception:
    pass

# The v2 origin can intermittently return Cloudflare 5xx responses. Install the
# bounded v1 fallback after strict activation so it wraps the final production
# context/odds methods, shares one process cache and never counts v1+v2 twice.
try:
    from app.services import (
        bzzoiro_v2_outage_fallback as _bzzoiro_v2_outage_fallback,
    )

    _bzzoiro_v2_outage_fallback.install()
except Exception:
    pass

# Native/source-matrix installers can replace provider methods later in preflight.
# Reassert the outage layer after they finish so the final runner still gets the
# circuit breaker and bounded v1 fallback.
try:
    from app.services import (
        bzzoiro_v2_outage_reassert as _bzzoiro_v2_outage_reassert,
    )

    _bzzoiro_v2_outage_reassert.install()
except Exception:
    pass

# Keep the strict 300-row selector on the same local-time horizon as the target
# expander. This installer also configures semantic merge drivers for generated
# runtime artifacts before the workflow's commit/rebase step.
try:
    from app.services import (
        strict_inventory_horizon_activation as _strict_inventory_horizon_activation,
    )

    _strict_inventory_horizon_activation.install()
except Exception:
    pass

# Strict inventory synchronization stores the authoritative independent source
# lists in metadata. Preserve those lists when the daily planner ranks the next
# deficit batch; fixture ids, aliases and proxy counts remain ineligible.
try:
    from app.services import (
        authoritative_coverage_planner_patch as _authoritative_coverage_planner_patch,
    )

    _authoritative_coverage_planner_patch.install()
except Exception:
    pass

# Discovery aliases can normalize to the same final semantic match key. Remove
# those duplicates before strict selection so all 300 slots represent different
# fixtures and provider assignments never repeat the same match.
try:
    from app.services import strict_unique_cohort_patch as _strict_unique_cohort_patch

    _strict_unique_cohort_patch.install()
except Exception:
    pass
