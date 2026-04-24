from __future__ import annotations

# Runtime patch hook kept intentionally safe.
# Previous package added the actual rescue prefilter instrumentation.
# This file exists so Run bot does not fail if the patch has already been applied.
from pathlib import Path

for path in [Path("artifacts/run-bot"), Path(".data/exports")]:
    path.mkdir(parents=True, exist_ok=True)

print("Rescue prefilter patch hook: OK")
