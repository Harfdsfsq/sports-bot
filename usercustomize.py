from sitecustomize import *

try:
    from app.services import day_inventory_runtime_guard
    day_inventory_runtime_guard.install()
except Exception:
    pass

try:
    from app.services import rapidapi_probe_runtime_guard
    rapidapi_probe_runtime_guard.install()
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
