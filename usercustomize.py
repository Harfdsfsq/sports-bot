from sitecustomize import *

try:
    from app.services import api_matching_quality_runtime_guard
    api_matching_quality_runtime_guard.install()
except Exception:
    pass

try:
    from app.services import sportlogic_query_runtime_guard
    sportlogic_query_runtime_guard.install()
except Exception:
    pass

try:
    from app.services import api_runtime_enhancements
    api_runtime_enhancements.install()
except Exception:
    pass

try:
    from app.services import day_inventory_runtime_guard
    day_inventory_runtime_guard.install()
except Exception:
    pass

try:
    from app.services import near_window_priority_runtime_patch
    near_window_priority_runtime_patch.install()
except Exception:
    pass

try:
    from app.services import context_family_matching_runtime_patch
    context_family_matching_runtime_patch.install()
except Exception:
    pass

try:
    from app.services import runner_step_trace
    runner_step_trace.install()
except Exception:
    pass

try:
    from app.services import lifecycle_sent_index_runtime_guard
    lifecycle_sent_index_runtime_guard.install()
except Exception:
    pass

try:
    from app.services import fixture_expansion_runtime_guard
    fixture_expansion_runtime_guard.install()
except Exception:
    pass

try:
    from app.services import api_health_runtime_guard
    api_health_runtime_guard.install()
except Exception:
    pass

try:
    from app.services import post_run_analytics_runtime_guard
    post_run_analytics_runtime_guard.install()
except Exception:
    pass

try:
    from app.services import rapidapi_probe_runtime_guard
    rapidapi_probe_runtime_guard.install()
except Exception:
    pass

try:
    from app.providers import rapidapi_odds_feed_patch
    rapidapi_odds_feed_patch.install()
except Exception:
    pass

try:
    from app.providers import rapidapi_bridge_runtime_patch
    rapidapi_bridge_runtime_patch.install()
except Exception:
    pass

try:
    from app.providers import sharpapi_runtime_patch
    sharpapi_runtime_patch.install()
except Exception:
    pass

try:
    from app.providers import sharpapi_official_patch
    sharpapi_official_patch.install()
except Exception:
    pass

try:
    from app.services import sharpapi_text_runtime_patch
    sharpapi_text_runtime_patch.install()
except Exception:
    pass
