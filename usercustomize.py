from sitecustomize import *

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
