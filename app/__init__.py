from __future__ import annotations

try:
    from . import runtime_bot_fix as _runtime_bot_fix  # noqa: F401
except Exception:
    _runtime_bot_fix = None
