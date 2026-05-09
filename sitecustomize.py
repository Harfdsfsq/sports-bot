from __future__ import annotations

"""Repository-wide startup hook for the main HARIZON runtime.

GitHub Actions starts the bot with `python - <<PY`, so Python imports this file
from the repository root before `app.cli` is executed.  Keep the root hook narrow:
install only the final Telegram pick safety guard.  Rich script-only patches stay
in `scripts/sitecustomize.py` for `python scripts/*.py` entrypoints.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

try:
    import telegram_controlled_pick_safety

    telegram_controlled_pick_safety.install()
except Exception as exc:
    try:
        print(f"root sitecustomize telegram safety skipped: {type(exc).__name__}: {exc}")
    except Exception:
        pass
